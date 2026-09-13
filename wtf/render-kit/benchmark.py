#!/usr/bin/env python3
"""W3 Render Kit — measured throughput benchmark for the L40S pilot box.

PLAN.md Phase 1 requires REAL numbers: VRAM fit + measured sec-per-10s-render
+ GPU-hours-per-hour, then a locked throughput table. This tool produces
``bench_receipt.json`` with exactly those numbers.

Metric formulas (documented in models.md / RUNBOOK.md):

    expected_output_seconds = (93 + (n-1)*80) / 25        # n = num_segments, avatar-v1.5
    sec_per_10s_render      = mean_wall_seconds * 10 / expected_output_seconds
    renders_per_gpu_hour    = 3600 / sec_per_10s_render
    gpu_hours_per_content_hour = mean_wall_seconds / expected_output_seconds

Modes:
    default      measured run: repeats the locked profile via run_avatar.py
    --dry-run    prints the exact commands a measured run would execute (offline)
    --self-test  fully offline: synthetic, clearly-labelled receipt + internal
                 consistency checks (no GPU, no network, no weights)

Exit codes: 0 ok | 1 receipt written but a gate FAILED | 2 blocked before running.

Stdlib-only; never imports torch.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import run_avatar as ra

RECEIPT_MARKER = "BENCH_RECEIPT "
DRY_RUN_MARKER = "BENCH_DRY_RUN "
SELF_TEST_MARKER = "BENCH_SELF_TEST "

SCHEMA_VERSION = 1
RECEIPT_TYPE = "wtf_avatar_renderkit_bench"

EXIT_OK = 0
EXIT_GATE_FAIL = 1
EXIT_BLOCKED = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Host + media probing (all local; failures degrade to None, never raise)
# ---------------------------------------------------------------------------

def _run(cmd, timeout=15):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    return None


def gpu_facts():
    raw = _run([
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total,compute_cap",
        "--format=csv,noheader,nounits",
    ])
    if not raw:
        return None
    first = raw.splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 4:
        return None
    return {
        "gpu_name": parts[0],
        "driver_version": parts[1],
        "vram_total_gb": round(float(parts[2]) / 1024.0, 2),
        "compute_cap": parts[3],
    }


def vram_used_mb():
    raw = _run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], timeout=10)
    if raw is None:
        return None
    try:
        return float(raw.splitlines()[0].strip())
    except (ValueError, IndexError):
        return None


class VramPoller:
    """Samples used VRAM in a background thread; .peak_gb is None if never sampled."""

    def __init__(self, interval=2.0):
        self.interval = interval
        self.peak_mb = None
        self._stop = threading.Event()
        self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            sample = vram_used_mb()
            if sample is not None:
                self.peak_mb = sample if self.peak_mb is None else max(self.peak_mb, sample)
            self._stop.wait(self.interval)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return self.peak_mb

    @property
    def peak_gb(self):
        return None if self.peak_mb is None else round(self.peak_mb / 1024.0, 2)


def probe_video(path):
    """ffprobe the produced file (duration + fps). None if ffprobe/file absent."""
    if not shutil.which("ffprobe") or not Path(path).is_file():
        return None
    out = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ], timeout=30)
    if not out:
        return None
    try:
        data = json.loads(out)
        info: dict = {"duration_seconds": None, "fps": None, "size_bytes": None}
        fmt = data.get("format", {})
        if fmt.get("duration") is not None:
            info["duration_seconds"] = round(float(fmt["duration"]), 3)
        if fmt.get("size") is not None:
            info["size_bytes"] = int(fmt["size"])
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                rate = stream.get("avg_frame_rate") or "0/0"
                num, _, den = rate.partition("/")
                if float(den or 0) != 0:
                    info["fps"] = round(float(num) / float(den), 3)
                break
        return info
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Measured execution
# ---------------------------------------------------------------------------

def execute_repeat(plan, repeat_index, timeout_s, log_path, stream=True):
    """Run one repeat of `plan`. Returns a measurement dict.

    Output is pumped by a reader thread; the main loop enforces the timeout
    even when the child produces no output (a hung render must not block).
    """
    argv = plan["argv"]
    env = dict(os.environ)
    env.update(plan["env_overrides"])
    env["PYTHONUNBUFFERED"] = "1"

    poller = VramPoller(interval=2.0).start()
    started = time.time()
    timed_out = False

    log_fp = open(log_path, "w", encoding="utf-8")

    def _pump():
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                log_fp.write(line)
                log_fp.flush()
                if stream:
                    sys.stdout.write(line)
                    sys.stdout.flush()
        except Exception:  # noqa: BLE001 - never let the pump thread crash the run
            pass

    proc = subprocess.Popen(
        argv, cwd=plan["cwd"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()

    deadline = started + float(timeout_s)
    while True:
        if proc.poll() is not None:
            break
        if time.time() > deadline:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=15)
            break
        time.sleep(0.5)

    reader.join(timeout=15)
    log_fp.close()
    wall = round(time.time() - started, 3)
    peak_gb = poller.stop()

    output_dir = Path(plan["output_dir"])
    final_name = plan["final_output_file"]
    final_path = output_dir / final_name
    present = final_path.is_file()
    probe = probe_video(final_path) if present else None

    log_tail = ""
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        log_tail = "\n".join(lines[-8:])[-2000:]
    except OSError:
        pass

    return {
        "repeat": repeat_index,
        "wall_seconds": wall,
        "exit_code": None if timed_out else proc.returncode,
        "timed_out": timed_out,
        "peak_vram_gb": peak_gb,
        "output_video": str(final_path),
        "output_present": present,
        "probe": probe,
        "log_path": str(log_path),
        "log_tail": log_tail,
    }


# ---------------------------------------------------------------------------
# Metrics + gates
# ---------------------------------------------------------------------------

def compute_metrics(measurements, expected_seconds):
    walls = [m["wall_seconds"] for m in measurements]
    mean_wall = sum(walls) / len(walls) if walls else None
    peaks = [m["peak_vram_gb"] for m in measurements if m.get("peak_vram_gb") is not None]
    metrics = {
        "runs": len(measurements),
        "mean_wall_seconds": round(mean_wall, 3) if mean_wall is not None else None,
        "expected_output_seconds": round(expected_seconds, 3),
        "peak_vram_gb": round(max(peaks), 2) if peaks else None,
        "sec_per_10s_render": None,
        "renders_per_gpu_hour": None,
        "gpu_hours_per_content_hour": None,
    }
    if mean_wall is not None and expected_seconds > 0:
        metrics["sec_per_10s_render"] = round(mean_wall * 10.0 / expected_seconds, 3)
        metrics["renders_per_gpu_hour"] = round(3600.0 / metrics["sec_per_10s_render"], 3)
        metrics["gpu_hours_per_content_hour"] = round(mean_wall / expected_seconds, 4)
    return metrics


def evaluate_gates(metrics, measurements, budgets):
    gates = []

    zero_exit = all((m["exit_code"] == 0) and not m["timed_out"] for m in measurements)
    gates.append({
        "id": "runs_exit_zero",
        "status": "PASS" if zero_exit else "FAIL",
        "detail": "%d/%d repeats exited 0 without timeout"
                  % (sum(1 for m in measurements if m["exit_code"] == 0 and not m["timed_out"]),
                     len(measurements)),
    })

    outputs = all(m["output_present"] for m in measurements)
    gates.append({
        "id": "outputs_present",
        "status": "PASS" if outputs else "FAIL",
        "detail": "%d/%d repeats produced the final mp4"
                  % (sum(1 for m in measurements if m["output_present"]), len(measurements)),
    })

    budget_vram = budgets.get("peak_vram_gb")
    if budget_vram is None:
        gates.append({"id": "peak_vram_within_budget", "status": "BASELINE_PENDING",
                      "detail": "no VRAM budget set; first measured run must lock it"})
    elif metrics["peak_vram_gb"] is None:
        gates.append({"id": "peak_vram_within_budget", "status": "FAIL",
                      "detail": "no VRAM samples captured (nvidia-smi unavailable)"})
    else:
        ok = metrics["peak_vram_gb"] <= budget_vram
        gates.append({
            "id": "peak_vram_within_budget",
            "status": "PASS" if ok else "FAIL",
            "detail": "peak %.2f GB vs budget %.2f GB" % (metrics["peak_vram_gb"], budget_vram),
        })

    budget_secs = budgets.get("sec_per_10s_render")
    if budget_secs is None:
        gates.append({"id": "sec_per_10s_within_budget", "status": "BASELINE_PENDING",
                      "detail": "no throughput budget set; lock after first measured run"})
    elif metrics["sec_per_10s_render"] is None:
        gates.append({"id": "sec_per_10s_within_budget", "status": "FAIL",
                      "detail": "no wall-clock metrics (no completed repeats)"})
    else:
        ok = metrics["sec_per_10s_render"] <= budget_secs
        gates.append({
            "id": "sec_per_10s_within_budget",
            "status": "PASS" if ok else "FAIL",
            "detail": "%.1f s per 10s render vs budget %.1f s"
                      % (metrics["sec_per_10s_render"], budget_secs),
        })

    return gates


def next_actions(gates, mode):
    actions = []
    statuses = {g["id"]: g["status"] for g in gates}
    if mode == "self_test":
        actions.append("Synthetic receipt only — run `benchmark.py` on the pilot box to get real numbers.")
    if any(s == "BASELINE_PENDING" for s in statuses.values()):
        actions.append("Lock budgets from the first measured run: re-run with "
                       "--budget-sec-per-10s and --budget-peak-vram-gb set, then record the "
                       "throughput table in RUNBOOK.md.")
    if statuses.get("runs_exit_zero") == "FAIL":
        actions.append("Inspect per-repeat logs (receipt measurements[].log_tail) before changing anything.")
    if statuses.get("peak_vram_within_budget") == "FAIL":
        actions.append("VRAM over budget: do NOT add extra processes; verify int8 path is actually loaded.")
    if statuses.get("sec_per_10s_within_budget") == "FAIL":
        actions.append("Throughput miss: re-check `--use_int8` + `--use_distill` flags and GPU thermals "
                       "before scaling horizontally.")
    if not actions:
        actions.append("All gates green — record the throughput table in RUNBOOK.md and proceed to W6 capture.")
    return actions


def write_receipt(path, receipt, force=False):
    """Fail-closed guard: a self-test receipt must never clobber a measured one."""
    path = Path(path)
    superseded = False
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing = {}
        if existing and (existing.get("synthetic") is False) and receipt.get("synthetic") is True:
            if not force:
                return False, ("refusing to overwrite measured receipt %s with a synthetic one "
                               "(pass --force or choose another --out)" % path)
        superseded = bool(existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt["superseded_previous"] = superseded
    path.write_text(json.dumps(receipt, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return True, "written to %s" % path


# ---------------------------------------------------------------------------
# Self-test (offline)
# ---------------------------------------------------------------------------

def _synthetic_measurements(config_id, walls, peak_gb, expected_seconds, stage_1, out_dir):
    rows = []
    for i, wall in enumerate(walls):
        rows.append({
            "config_id": config_id,
            "repeat": i,
            "wall_seconds": wall,
            "exit_code": 0,
            "timed_out": False,
            "peak_vram_gb": peak_gb,
            "output_video": str(Path(out_dir) / ("SYNTHETIC_%s_%d.mp4" % (config_id, i))),
            "output_present": True,
            "synthetic": True,
            "expected_output_seconds": expected_seconds,
            "probe": {"duration_seconds": expected_seconds, "fps": 25.0, "size_bytes": None},
            "log_path": None,
            "log_tail": None,
        })
    return rows


def build_self_test_receipt(out_dir=Path(".")):
    """Two synthetic configs: one PASSing every gate, one deliberately failing."""
    expected = ra.expected_output_seconds(3)  # ~10.12 s at avatar-v1.5 constants
    budgets = {"sec_per_10s_render": 45.0, "peak_vram_gb": 46.0}

    configs = []
    for cfg_id, walls, peak in (
        ("l40s_int8_distill_cp1", [30.0, 31.0], 41.2),
        ("l40s_int8_distill_cp1_720p_probe", [58.0, 60.0], 47.9),
    ):
        rows = _synthetic_measurements(cfg_id, walls, peak, expected, "ai2v", out_dir)
        configs.append({
            "config_id": cfg_id,
            "expected_output_seconds": round(expected, 3),
            "measurements": rows,
            "metrics": compute_metrics(rows, expected),
        })
        configs[-1]["gates"] = evaluate_gates(configs[-1]["metrics"], rows, budgets)

    primary = configs[0]
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "created_at": now_iso(),
        "mode": "self_test",
        "synthetic": True,
        "measurement_source": "synthetic_fixture",
        "profile": ra.PROFILE,
        "host": None,
        "config": {
            "stage_1": "ai2v",
            "resolution": "480p",
            "num_segments": 3,
            "repeats": 2,
            "expected_output_seconds": round(expected, 3),
            "launcher": None,
        },
        "budgets": budgets,
        "configs": configs,
        "metrics": primary["metrics"],
        "gates": primary["gates"],
        "next_actions": next_actions(primary["gates"], "self_test"),
        "notes": [
            "SYNTHETIC receipt produced by --self-test: numbers are fixtures that exercise the "
            "metrics and gate logic, NOT measurements of real hardware.",
            "Do not use these numbers for capacity planning; run benchmark.py on the pilot box.",
        ],
    }
    return receipt


def run_self_test_checks(receipt):
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    required_top = ["schema_version", "receipt_type", "created_at", "mode", "synthetic",
                    "configs", "metrics", "gates", "budgets", "next_actions"]
    missing = [k for k in required_top if k not in receipt]
    check("receipt_required_keys", not missing, "missing: %s" % missing if missing else "all present")
    check("receipt_marked_synthetic",
          receipt.get("synthetic") is True and receipt.get("measurement_source") == "synthetic_fixture",
          "synthetic=%s source=%s" % (receipt.get("synthetic"), receipt.get("measurement_source")))

    arithmetic_ok, details = True, []
    for cfg in receipt["configs"]:
        exp = cfg["expected_output_seconds"]
        m = cfg["metrics"]
        if m["sec_per_10s_render"] is None:
            arithmetic_ok = False
            details.append("%s: no metrics" % cfg["config_id"])
            continue
        recomputed = round(m["mean_wall_seconds"] * 10.0 / exp, 3)
        if abs(recomputed - m["sec_per_10s_render"]) > 1e-6:
            arithmetic_ok = False
            details.append("%s: sec_per_10s mismatch" % cfg["config_id"])
        ghph = round(m["mean_wall_seconds"] / exp, 4)
        if abs(ghph - m["gpu_hours_per_content_hour"]) > 1e-9:
            arithmetic_ok = False
            details.append("%s: gpu_hours mismatch" % cfg["config_id"])
    check("metrics_arithmetic_recomputed", arithmetic_ok, "; ".join(details) or "consistent")

    statuses = [g["status"] for cfg in receipt["configs"] for g in cfg["gates"]]
    check("gate_paths_exercised", ("PASS" in statuses and "FAIL" in statuses),
          "statuses: %s" % sorted(set(statuses)))

    roundtrip_ok = False
    try:
        roundtrip_ok = json.loads(json.dumps(receipt)) == receipt
    except Exception:  # noqa: BLE001
        pass
    check("json_roundtrip", roundtrip_ok)

    ok = all(c["status"] == "PASS" for c in checks)
    return ok, checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark LongCat-Video-Avatar renders on the pilot L40S.")
    parser.add_argument("--self-test", action="store_true", help="Offline synthetic receipt + checks.")
    parser.add_argument("--dry-run", action="store_true", help="Print the measured run's commands; execute nothing.")
    parser.add_argument("--out", default="bench_receipt.json")
    parser.add_argument("--force", action="store_true",
                        help="Allow a synthetic receipt to overwrite a measured one.")
    parser.add_argument("--profile", default=ra.PROFILE, choices=[ra.PROFILE])
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--checkpoint-dir", default=None)
    parser.add_argument("--input-json", default=None)
    parser.add_argument("--output-dir", default=None, help="Render output dir (default: <repo>/outputs_bench).")
    parser.add_argument("--stage-1", default="ai2v", choices=["ai2v", "at2v"])
    parser.add_argument("--num-segments", type=int, default=3, help="3 segments ~= 10.1 s of output (avatar-v1.5).")
    parser.add_argument("--resolution", default="480p", choices=["480p", "720p"])
    parser.add_argument("--ref-img-index", type=int, default=10)
    parser.add_argument("--mask-frame-range", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--budget-sec-per-10s", type=float, default=None)
    parser.add_argument("--budget-peak-vram-gb", type=float, default=46.0)
    parser.add_argument("--timeout-s", type=int, default=1800)
    parser.add_argument("--launcher", default="torchrun", choices=["torchrun", "direct"])
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--torchrun-exe", default=None)
    parser.add_argument("--skip-preflight", action="store_true",
                        help="Skip checkpoint-layout checks (tests only; unsafe for real runs).")
    return parser.parse_args(argv)


def _resolve_paths(args):
    repo_root = Path(args.repo_root).resolve() if args.repo_root else ra.default_repo_root()
    checkpoint_dir = Path(args.checkpoint_dir).resolve() if args.checkpoint_dir \
        else (repo_root / "weights" / ra.AVATAR_DIRNAME)
    input_json = Path(args.input_json) if args.input_json else (repo_root / "assets/avatar/single_example_1.json")
    if not input_json.is_absolute():
        input_json = repo_root / input_json
    output_dir = Path(args.output_dir).resolve() if args.output_dir else (repo_root / "outputs_bench")
    return repo_root, checkpoint_dir, input_json.resolve(), output_dir


def _build_plan_for_args(args, repo_root, checkpoint_dir, input_json, output_dir):
    if not args.torchrun_exe:
        sibling = Path(args.python_exe).resolve().parent / "torchrun"
        args.torchrun_exe = str(sibling) if sibling.exists() else (shutil.which("torchrun") or "torchrun")
    return ra.build_plan(
        repo_root=repo_root,
        checkpoint_dir=checkpoint_dir,
        input_json=input_json,
        output_dir=output_dir,
        stage_1=args.stage_1,
        num_segments=args.num_segments,
        resolution=args.resolution,
        ref_img_index=args.ref_img_index,
        mask_frame_range=args.mask_frame_range,
        launcher=args.launcher,
        python_exe=args.python_exe,
        torchrun_exe=args.torchrun_exe,
        master_port=29513,
    )


def main(argv=None) -> int:
    args = _parse_args(argv)
    repo_root, checkpoint_dir, input_json, output_dir = _resolve_paths(args)

    if args.self_test:
        receipt = build_self_test_receipt(out_dir=output_dir)
        ok, checks = run_self_test_checks(receipt)
        receipt["self_test_checks"] = checks
        written, note = write_receipt(args.out, receipt, force=args.force)
        if not written:
            print("ERROR: " + note, file=sys.stderr)
            return EXIT_BLOCKED
        print(SELF_TEST_MARKER + json.dumps({"ok": ok, "checks": checks, "receipt": str(Path(args.out).resolve())}))
        if not ok:
            print("SELF-TEST FAILED", file=sys.stderr)
            return 1
        return EXIT_OK

    if args.repeats < 1:
        print("ERROR: --repeats must be >= 1", file=sys.stderr)
        return EXIT_BLOCKED

    plan = _build_plan_for_args(args, repo_root, checkpoint_dir, input_json, output_dir)

    if args.dry_run:
        summary = {
            "mode": "dry_run",
            "profile": args.profile,
            "repeats": args.repeats,
            "num_segments": args.num_segments,
            "expected_output_seconds": plan["expected_output_seconds"],
            "commands": [plan["argv"]] * args.repeats,
            "output_dir": str(output_dir),
            "receipt_out": str(Path(args.out).resolve()),
        }
        print(DRY_RUN_MARKER + json.dumps(summary))
        return EXIT_OK

    # Measured mode — fail closed before touching the GPU.
    if not args.skip_preflight:
        present, missing = ra.check_checkpoint_layout(checkpoint_dir)
        if missing:
            print("ERROR: checkpoint layout incomplete at %s (run download_weights.sh first)"
                  % checkpoint_dir, file=sys.stderr)
            for item in missing:
                print("  - missing " + item, file=sys.stderr)
            return EXIT_BLOCKED

    if not input_json.exists():
        print("ERROR: input json not found: %s" % input_json, file=sys.stderr)
        return EXIT_BLOCKED

    expected = ra.expected_output_seconds(args.num_segments)
    output_dir.mkdir(parents=True, exist_ok=True)

    measurements = []
    for i in range(args.repeats):
        log_path = output_dir / ("bench_repeat_%d.log" % (i + 1))
        print("[bench] repeat %d/%d (expected output %.2fs)" % (i + 1, args.repeats, expected))
        row = execute_repeat(plan, i, args.timeout_s, log_path)
        row["config_id"] = args.profile
        measurements.append(row)

    metrics = compute_metrics(measurements, expected)
    budgets = {"sec_per_10s_render": args.budget_sec_per_10s, "peak_vram_gb": args.budget_peak_vram_gb}
    gates = evaluate_gates(metrics, measurements, budgets)

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "created_at": now_iso(),
        "mode": "measured",
        "synthetic": False,
        "measurement_source": "nvidia-smi+wall_clock",
        "profile": args.profile,
        "host": gpu_facts(),
        "config": {
            "stage_1": args.stage_1,
            "resolution": args.resolution,
            "num_segments": args.num_segments,
            "repeats": args.repeats,
            "expected_output_seconds": round(expected, 3),
            "repo_root": str(repo_root),
            "checkpoint_dir": str(checkpoint_dir),
            "input_json": str(input_json),
            "output_dir": str(output_dir),
            "launcher": args.launcher,
            "timeout_s": args.timeout_s,
        },
        "budgets": budgets,
        "configs": [{"config_id": args.profile, "expected_output_seconds": round(expected, 3),
                     "measurements": measurements, "metrics": metrics, "gates": gates}],
        "metrics": metrics,
        "gates": gates,
        "next_actions": next_actions(gates, "measured"),
        "notes": ["wall_seconds is the full run_avatar.py invocation (model load + render + encode).",
                  "peak_vram_gb sampled via nvidia-smi every 2s during the run."],
    }

    written, note = write_receipt(args.out, receipt, force=args.force)
    if not written:
        print("ERROR: " + note, file=sys.stderr)
        return EXIT_BLOCKED

    print(RECEIPT_MARKER + json.dumps({"receipt": str(Path(args.out).resolve()),
                                       "metrics": metrics, "gates": gates}))
    failed = [g for g in gates if g["status"] == "FAIL"]
    return EXIT_GATE_FAIL if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
