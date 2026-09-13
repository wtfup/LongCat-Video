"""W3 Render Kit — benchmark.py tests (fully offline: no GPU, no network)."""

import json
import sys
from pathlib import Path

import pytest

import benchmark as bench
import run_avatar as ra


def _row(wall, peak, exit_code=0, present=True, repeat=0):
    return {
        "repeat": repeat,
        "wall_seconds": wall,
        "exit_code": exit_code,
        "timed_out": False,
        "peak_vram_gb": peak,
        "output_video": "/tmp/x.mp4",
        "output_present": present,
        "probe": None,
        "log_path": None,
        "log_tail": "",
    }


# ---------------------------------------------------------------------------
# Metrics arithmetic
# ---------------------------------------------------------------------------

def test_compute_metrics_formula():
    expected = ra.expected_output_seconds(3)  # 10.12 s
    rows = [_row(30.0, 41.0, repeat=0), _row(31.0, 41.2, repeat=1)]
    m = bench.compute_metrics(rows, expected)
    assert m["runs"] == 2
    assert m["mean_wall_seconds"] == pytest.approx(30.5)
    assert m["peak_vram_gb"] == pytest.approx(41.2)
    assert m["sec_per_10s_render"] == pytest.approx(round(30.5 * 10.0 / expected, 3))
    assert m["renders_per_gpu_hour"] == pytest.approx(round(3600.0 / m["sec_per_10s_render"], 3))
    assert m["gpu_hours_per_content_hour"] == pytest.approx(round(30.5 / expected, 4))


def test_compute_metrics_handles_no_vram_samples():
    m = bench.compute_metrics([_row(30.0, None)], ra.expected_output_seconds(3))
    assert m["peak_vram_gb"] is None
    assert m["sec_per_10s_render"] is not None


# ---------------------------------------------------------------------------
# Gates: PASS / FAIL / BASELINE_PENDING
# ---------------------------------------------------------------------------

def test_gates_baseline_pending_when_no_budgets():
    rows = [_row(30.0, 41.0)]
    metrics = bench.compute_metrics(rows, ra.expected_output_seconds(3))
    gates = bench.evaluate_gates(metrics, rows, {"sec_per_10s_render": None, "peak_vram_gb": None})
    statuses = {g["id"]: g["status"] for g in gates}
    assert statuses["sec_per_10s_within_budget"] == "BASELINE_PENDING"
    assert statuses["peak_vram_within_budget"] == "BASELINE_PENDING"
    assert statuses["runs_exit_zero"] == "PASS"
    assert statuses["outputs_present"] == "PASS"


def test_gates_pass_and_fail_paths():
    expected = ra.expected_output_seconds(3)
    budgets = {"sec_per_10s_render": 45.0, "peak_vram_gb": 46.0}

    good = [_row(30.0, 41.0), _row(31.0, 41.2, repeat=1)]
    gates = {g["id"]: g for g in bench.evaluate_gates(bench.compute_metrics(good, expected), good, budgets)}
    assert all(g["status"] == "PASS" for g in gates.values())

    slow = [_row(58.0, 47.9)]
    gates = {g["id"]: g for g in bench.evaluate_gates(bench.compute_metrics(slow, expected), slow, budgets)}
    assert gates["sec_per_10s_within_budget"]["status"] == "FAIL"
    assert gates["peak_vram_within_budget"]["status"] == "FAIL"

    crashed = [_row(10.0, 40.0, exit_code=1, present=False)]
    gates = {g["id"]: g for g in bench.evaluate_gates(bench.compute_metrics(crashed, expected), crashed, budgets)}
    assert gates["runs_exit_zero"]["status"] == "FAIL"
    assert gates["outputs_present"]["status"] == "FAIL"


def test_gate_fails_closed_when_vram_never_sampled():
    rows = [_row(30.0, None)]
    metrics = bench.compute_metrics(rows, ra.expected_output_seconds(3))
    gates = {g["id"]: g for g in bench.evaluate_gates(metrics, rows,
                                                      {"sec_per_10s_render": 45.0, "peak_vram_gb": 46.0})}
    assert gates["peak_vram_within_budget"]["status"] == "FAIL"
    assert "nvidia-smi" in gates["peak_vram_within_budget"]["detail"]


# ---------------------------------------------------------------------------
# Self-test mode (offline) + receipt guard
# ---------------------------------------------------------------------------

def test_self_test_receipt_is_labelled_and_checks_pass(tmp_path):
    receipt = bench.build_self_test_receipt(out_dir=tmp_path)
    assert receipt["synthetic"] is True
    assert receipt["measurement_source"] == "synthetic_fixture"
    assert receipt["mode"] == "self_test"
    ok, checks = bench.run_self_test_checks(receipt)
    assert ok, checks
    # both gate paths exercised on purpose
    statuses = [g["status"] for cfg in receipt["configs"] for g in cfg["gates"]]
    assert "PASS" in statuses and "FAIL" in statuses


def test_self_test_arithmetic_check_catches_tampering(tmp_path):
    receipt = bench.build_self_test_receipt(out_dir=tmp_path)
    receipt["configs"][0]["metrics"]["sec_per_10s_render"] += 1.0
    ok, checks = bench.run_self_test_checks(receipt)
    assert not ok
    assert any(c["check"] == "metrics_arithmetic_recomputed" and c["status"] == "FAIL" for c in checks)


def test_self_test_cli_writes_receipt_and_never_touches_gpu_or_network(tmp_path, capsys, monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - only hit on regression
        raise AssertionError("self-test must not run subprocesses")

    monkeypatch.setattr(bench.subprocess, "run", _boom)
    monkeypatch.setattr(bench.subprocess, "Popen", _boom)

    out = tmp_path / "bench_receipt.json"
    rc = bench.main(["--self-test", "--out", str(out)])
    assert rc == bench.EXIT_OK
    assert out.is_file()
    receipt = json.loads(out.read_text())
    assert receipt["synthetic"] is True
    assert all(c["status"] == "PASS" for c in receipt["self_test_checks"])
    assert capsys.readouterr().out.startswith(bench.SELF_TEST_MARKER)


def test_self_test_refuses_to_overwrite_a_measured_receipt(tmp_path, capsys):
    out = tmp_path / "bench_receipt.json"
    out.write_text(json.dumps({"synthetic": False, "mode": "measured"}))

    rc = bench.main(["--self-test", "--out", str(out)])
    assert rc == bench.EXIT_BLOCKED
    assert json.loads(out.read_text())["synthetic"] is False  # untouched
    assert "refusing to overwrite" in capsys.readouterr().err

    rc = bench.main(["--self-test", "--out", str(out), "--force"])
    assert rc == bench.EXIT_OK
    assert json.loads(out.read_text())["synthetic"] is True


def test_write_receipt_guard_unit(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"synthetic": False}))
    ok, note = bench.write_receipt(path, {"synthetic": True})
    assert not ok and "refusing" in note
    ok, _ = bench.write_receipt(path, {"synthetic": True}, force=True)
    assert ok
    # a measured receipt may replace a measured receipt without --force
    path.write_text(json.dumps({"synthetic": False, "old": True}))
    ok, _ = bench.write_receipt(path, {"synthetic": False, "mode": "measured"})
    assert ok and json.loads(path.read_text())["superseded_previous"] is True


# ---------------------------------------------------------------------------
# Dry-run + measured plumbing (stub subprocesses only)
# ---------------------------------------------------------------------------

def test_dry_run_prints_commands_and_executes_nothing(fake_repo, tmp_path, capsys, monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - only hit on regression
        raise AssertionError("dry-run must not run subprocesses")

    monkeypatch.setattr(bench.subprocess, "run", _boom)
    monkeypatch.setattr(bench.subprocess, "Popen", _boom)

    out = tmp_path / "receipt.json"
    rc = bench.main(["--dry-run", "--repo-root", str(fake_repo), "--repeats", "2",
                     "--out", str(out)])
    assert rc == bench.EXIT_OK
    assert not out.exists()
    text = capsys.readouterr().out
    line = next(l for l in text.splitlines() if l.startswith(bench.DRY_RUN_MARKER))
    summary = json.loads(line[len(bench.DRY_RUN_MARKER):])
    assert len(summary["commands"]) == 2
    assert "--use_int8" in " ".join(summary["commands"][0])


def _stub_plan(tmp_path, code, output_name="video_continue_3.mp4"):
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    return {
        "argv": [sys.executable, "-c", code],
        "cwd": str(tmp_path),
        "env_overrides": {},
        "output_dir": str(out_dir),
        "final_output_file": output_name,
    }


def test_execute_repeat_captures_success(tmp_path, capsys):
    plan = _stub_plan(tmp_path, "print('hello from stub render')")
    log = tmp_path / "r1.log"
    row = bench.execute_repeat(plan, 0, timeout_s=60, log_path=log, stream=True)
    assert row["exit_code"] == 0
    assert row["timed_out"] is False
    assert row["wall_seconds"] > 0
    assert row["output_present"] is False
    assert row["probe"] is None
    assert "hello from stub render" in row["log_tail"]
    assert "hello from stub render" in log.read_text()


def test_execute_repeat_detects_output_file(tmp_path):
    final = tmp_path / "out" / "video_continue_3.mp4"
    code = "import pathlib; pathlib.Path(%r).write_bytes(b'v')" % str(final)
    plan = _stub_plan(tmp_path, code)
    row = bench.execute_repeat(plan, 0, timeout_s=60, log_path=tmp_path / "r2.log", stream=False)
    assert row["output_present"] is True
    assert row["output_video"] == str(final)


def test_execute_repeat_enforces_timeout_on_silent_child(tmp_path):
    plan = _stub_plan(tmp_path, "import time; time.sleep(60)")
    row = bench.execute_repeat(plan, 0, timeout_s=2, log_path=tmp_path / "r3.log", stream=False)
    assert row["timed_out"] is True
    assert row["exit_code"] is None
    assert row["wall_seconds"] < 30


def test_measured_mode_blocked_without_checkpoints(fake_repo, tmp_path, capsys):
    import shutil
    shutil.rmtree(fake_repo / "weights")
    rc = bench.main(["--repo-root", str(fake_repo), "--out", str(tmp_path / "r.json")])
    assert rc == bench.EXIT_BLOCKED
    assert not (tmp_path / "r.json").exists()
    assert "checkpoint layout incomplete" in capsys.readouterr().err


def test_next_actions_mentions_baseline_lock():
    expected = ra.expected_output_seconds(3)
    rows = [_row(30.0, 41.0)]
    metrics = bench.compute_metrics(rows, expected)
    gates = bench.evaluate_gates(metrics, rows, {"sec_per_10s_render": None, "peak_vram_gb": None})
    actions = bench.next_actions(gates, "measured")
    assert any("throughput table" in a for a in actions)
