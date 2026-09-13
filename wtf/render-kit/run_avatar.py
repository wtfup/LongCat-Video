#!/usr/bin/env python3
"""W3 Render Kit — single-L40S launcher for LongCat-Video-Avatar 1.5.

Locked deployment profile for the g6e.xlarge pilot box (1x L40S 48 GB):

    model_type=avatar-v1.5  --use_int8  --use_distill  --context_parallel_size=1

Why these flags (upstream-verified, see models.md for citations):
  - `--model_type avatar-v1.5` uses the Whisper-large-v3 audio encoder.
  - `--use_distill` is REQUIRED for v1.5 (README: "Distillation mode ...
    required when using --model_type avatar-v1.5").
  - `--use_int8` loads the quantized DiT (`base_model_int8/`) for reduced VRAM.
  - `context_parallel_size=1` because the pilot box has a single GPU
    (upstream examples with cp=2 are for 2-GPU boxes).

This wrapper is stdlib-only: it never imports torch. It validates the local
checkpoint layout, builds the exact upstream command, and (unless --dry-run)
executes it via torchrun from the repo root. Relative asset paths inside the
input JSON resolve against the repo root because the upstream demo runs with
cwd=repo root.

Exit codes: 0 ok | 2 preflight failure | other = upstream demo exit code.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (must stay in sync with upstream run_demo_avatar_single_audio_to_video.py)
# ---------------------------------------------------------------------------

UPSTREAM_SCRIPT = "run_demo_avatar_single_audio_to_video.py"
FOUNDATION_DIRNAME = "LongCat-Video"                 # tokenizer/te + vae live here
AVATAR_DIRNAME = "LongCat-Video-Avatar-1.5"          # default checkpoint dir
PROFILE = "l40s_int8_distill_cp1"

PLAN_MARKER = "RENDER_KIT_PLAN "
RESULT_MARKER = "RENDER_KIT_RESULT "
INPUT_JSON_MARKER = "RENDER_KIT_INPUT_JSON "

EXIT_OK = 0
EXIT_PREFLIGHT = 2

# Sampling constants used for expected-output arithmetic (documented in
# models.md; read off upstream lines 77-83 of run_demo_avatar_single_audio_to_video.py).
NUM_FRAMES = 93
NUM_COND_FRAMES = 13
SAVE_FPS = 25


def default_repo_root() -> Path:
    """<repo>/wtf/render-kit/run_avatar.py -> <repo>."""
    return Path(__file__).resolve().parents[2]


def expected_output_seconds(num_segments: int) -> float:
    """Duration of the FINAL assembled video the upstream demo writes."""
    n = max(1, int(num_segments))
    frames = NUM_FRAMES + (n - 1) * (NUM_FRAMES - NUM_COND_FRAMES)
    return frames / SAVE_FPS


def expected_output_files(stage_1: str, num_segments: int) -> list:
    """Names the upstream demo writes into --output_dir."""
    n = max(1, int(num_segments))
    names = ["%s_demo_1.mp4" % stage_1]
    for seg in range(2, n + 1):
        names.append("video_continue_%d.mp4" % seg)
    return names


def final_output_file(stage_1: str, num_segments: int) -> str:
    names = expected_output_files(stage_1, num_segments)
    return names[-1]


# ---------------------------------------------------------------------------
# Preflight (offline, fail-closed)
# ---------------------------------------------------------------------------

def check_checkpoint_layout(checkpoint_dir: Path):
    """Verify everything upstream loads exists. Returns (present, missing).

    Paths mirror run_demo_avatar_single_audio_to_video.py:
      * base_model_int8/config.json + quantized shards   (--use_int8)
      * lora/dmd_lora.safetensors                        (--use_distill, v1.5)
      * scheduler/                                       (v1.5 scheduler)
      * whisper-large-v3/                                (v1.5 audio encoder)
      * vocal_separator/Kim_Vocal_2.onnx                 (vocal separation)
      * ../LongCat-Video/{tokenizer,text_encoder,vae}    (shared foundation)
    """
    checkpoint_dir = Path(checkpoint_dir)
    present, missing = [], []

    def need(path: Path, what: str):
        if path.exists():
            present.append(str(path))
        else:
            missing.append("%s (expected: %s)" % (what, path))

    def need_nonempty_dir(path: Path, what: str):
        if path.is_dir() and any(path.iterdir()):
            present.append(str(path))
        else:
            missing.append("%s (expected non-empty dir: %s)" % (what, path))

    need(checkpoint_dir / "base_model_int8" / "config.json", "int8 DiT config")

    int8_dir = checkpoint_dir / "base_model_int8"
    index_json = int8_dir / "quantized_model.safetensors.index.json"
    shards = sorted(int8_dir.glob("*.safetensors")) if int8_dir.is_dir() else []
    if index_json.exists() or shards:
        present.append(str(index_json if index_json.exists() else shards[0]))
    else:
        missing.append(
            "int8 DiT weights (expected %s or *.safetensors shards in %s)"
            % (index_json, int8_dir)
        )

    need(checkpoint_dir / "lora" / "dmd_lora.safetensors", "distill LoRA (dmd)")
    need_nonempty_dir(checkpoint_dir / "scheduler", "v1.5 scheduler dir")
    need_nonempty_dir(checkpoint_dir / "whisper-large-v3", "Whisper-large-v3 audio encoder")
    need(checkpoint_dir / "vocal_separator" / "Kim_Vocal_2.onnx", "vocal separator model")

    foundation = checkpoint_dir.parent / FOUNDATION_DIRNAME
    for sub in ("tokenizer", "text_encoder", "vae"):
        need_nonempty_dir(foundation / sub, "foundation %s" % sub)

    return present, missing


def load_and_validate_input_json(path: Path, stage_1: str):
    """Returns (data, errors). Fails closed on missing/malformed fields."""
    errors = []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report exact failure
        return None, ["input json unreadable: %s (%s)" % (path, exc)]

    if not isinstance(data, dict):
        return None, ["input json top-level must be an object"]

    if not isinstance(data.get("prompt"), str) or not data["prompt"].strip():
        errors.append("input json missing non-empty string 'prompt'")

    cond_audio = data.get("cond_audio")
    if not isinstance(cond_audio, dict) or not (
        isinstance(cond_audio.get("person1"), str) and cond_audio["person1"].strip()
    ):
        errors.append("input json missing 'cond_audio.person1' audio path")

    if stage_1 == "ai2v":
        if not isinstance(data.get("cond_image"), str) or not data["cond_image"].strip():
            errors.append("input json missing 'cond_image' (required for stage_1=ai2v)")

    return data, errors


def check_referenced_files(data, repo_root: Path, stage_1: str):
    """Referenced media must exist; relative paths resolve against repo root."""
    missing = []

    def resolve(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else (repo_root / q)

    audio = resolve(data["cond_audio"]["person1"])
    if not audio.exists():
        missing.append("audio not found: %s" % audio)
    if stage_1 == "ai2v":
        image = resolve(data["cond_image"])
        if not image.exists():
            missing.append("cond_image not found: %s" % image)
    return missing


# ---------------------------------------------------------------------------
# Command planning
# ---------------------------------------------------------------------------

def build_plan(
    repo_root: Path,
    checkpoint_dir: Path,
    input_json: Path,
    output_dir: Path,
    stage_1: str,
    num_segments: int,
    resolution: str,
    ref_img_index: int,
    mask_frame_range: int,
    launcher: str,
    python_exe: str,
    torchrun_exe: str,
    master_port: int,
) -> dict:
    """Pure function: build the exact execution plan (no side effects)."""
    demo = Path(repo_root) / UPSTREAM_SCRIPT
    demo_args = [
        "--checkpoint_dir", str(checkpoint_dir),
        "--input_json", str(input_json),
        "--output_dir", str(output_dir),
        "--model_type", "avatar-v1.5",
        "--stage_1", stage_1,
        "--resolution", resolution,
        "--num_segments", str(int(num_segments)),
        "--ref_img_index", str(int(ref_img_index)),
        "--mask_frame_range", str(int(mask_frame_range)),
        "--context_parallel_size", "1",
        "--use_distill",
        "--use_int8",
    ]

    env_overrides = {}
    if launcher == "torchrun":
        argv = [
            torchrun_exe, "--standalone", "--nnodes=1", "--nproc_per_node=1",
            str(demo), *demo_args,
        ]
    else:  # direct launch: upstream reads RANK from env, so set the rendezvous vars
        argv = [python_exe, str(demo), *demo_args]
        env_overrides = {
            "RANK": "0",
            "LOCAL_RANK": "0",
            "WORLD_SIZE": "1",
            "MASTER_ADDR": "127.0.0.1",
            "MASTER_PORT": str(int(master_port)),
        }

    return {
        "profile": PROFILE,
        "repo_root": str(repo_root),
        "cwd": str(repo_root),
        "upstream_script": str(demo),
        "checkpoint_dir": str(checkpoint_dir),
        "input_json": str(input_json),
        "output_dir": str(output_dir),
        "stage_1": stage_1,
        "resolution": resolution,
        "num_segments": int(num_segments),
        "expected_output_seconds": expected_output_seconds(num_segments),
        "expected_output_files": expected_output_files(stage_1, num_segments),
        "final_output_file": final_output_file(stage_1, num_segments),
        "launcher": launcher,
        "argv": argv,
        "env_overrides": env_overrides,
    }


def write_input_json(out_path: Path, prompt: str, audio: str, image, stage_1: str) -> dict:
    """Materialize an upstream-format input JSON (used by the factory queue)."""
    payload = {
        "prompt": prompt,
        "cond_audio": {"person1": audio},
    }
    if stage_1 == "ai2v":
        payload["cond_image"] = image
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def resolve_executables(args) -> None:
    """Fill torchrun default: prefer the sibling of --python-exe, then PATH."""
    if args.torchrun_exe:
        return
    sibling = Path(args.python_exe).resolve().parent / "torchrun"
    args.torchrun_exe = str(sibling) if sibling.exists() else (shutil.which("torchrun") or "torchrun")


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Render WTF avatar clips on a single L40S (int8 + distill + cp=1)."
    )
    parser.add_argument("--profile", default=PROFILE, choices=[PROFILE],
                        help="Locked deployment profile (only one supported).")
    parser.add_argument("--repo-root", default=None,
                        help="WTF fork repo root (default: auto-detected from this file).")
    parser.add_argument("--checkpoint-dir", default=None,
                        help="Avatar checkpoint dir (default: <repo>/weights/%s)." % AVATAR_DIRNAME)
    parser.add_argument("--input-json", default=None,
                        help="Upstream input JSON (default: <repo>/assets/avatar/single_example_1.json).")
    parser.add_argument("--output-dir", default=None,
                        help="Output dir (default: <repo>/outputs_avatar_single).")
    parser.add_argument("--stage-1", default="ai2v", choices=["ai2v", "at2v"])
    parser.add_argument("--num-segments", type=int, default=1)
    parser.add_argument("--resolution", default="480p", choices=["480p", "720p"])
    parser.add_argument("--ref-img-index", type=int, default=10)
    parser.add_argument("--mask-frame-range", type=int, default=3)
    parser.add_argument("--launcher", default="torchrun", choices=["torchrun", "direct"],
                        help="torchrun (README-style) or direct launch with RANK env vars.")
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--torchrun-exe", default=None)
    parser.add_argument("--master-port", type=int, default=29513)
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate + print the exact command; execute nothing.")
    parser.add_argument("--skip-preflight", action="store_true",
                        help="Skip checkpoint-layout checks (kept for tests; unsafe for real runs).")
    parser.add_argument("--make-input-json", default=None, metavar="PATH",
                        help="Write an upstream input JSON from --prompt/--audio/--image and exit.")
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--audio", default=None)
    parser.add_argument("--image", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else default_repo_root()

    if args.make_input_json:
        if not args.prompt or not args.audio or (args.stage_1 == "ai2v" and not args.image):
            print("ERROR: --make-input-json requires --prompt and --audio "
                  "(and --image when --stage-1=ai2v)", file=sys.stderr)
            return EXIT_PREFLIGHT
        payload = write_input_json(Path(args.make_input_json), args.prompt, args.audio,
                                   args.image, args.stage_1)
        print(INPUT_JSON_MARKER + json.dumps({"path": str(Path(args.make_input_json).resolve()),
                                              "payload": payload}))
        return EXIT_OK

    if args.num_segments < 1:
        print("ERROR: --num-segments must be >= 1", file=sys.stderr)
        return EXIT_PREFLIGHT

    checkpoint_dir = Path(args.checkpoint_dir).resolve() if args.checkpoint_dir \
        else (repo_root / "weights" / AVATAR_DIRNAME)
    input_json = Path(args.input_json) if args.input_json else (repo_root / "assets/avatar/single_example_1.json")
    if not input_json.is_absolute():
        input_json = repo_root / input_json
    input_json = input_json.resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir \
        else (repo_root / "outputs_avatar_single").resolve()

    if not (repo_root / UPSTREAM_SCRIPT).exists():
        print("ERROR: upstream script not found: %s (is --repo-root correct?)"
              % (repo_root / UPSTREAM_SCRIPT), file=sys.stderr)
        return EXIT_PREFLIGHT

    data, errors = load_and_validate_input_json(input_json, args.stage_1)
    if errors:
        print("ERROR: input json validation failed:", file=sys.stderr)
        for err in errors:
            print("  - " + err, file=sys.stderr)
        return EXIT_PREFLIGHT

    missing_media = check_referenced_files(data, repo_root, args.stage_1)
    if missing_media:
        print("ERROR: referenced media missing:", file=sys.stderr)
        for item in missing_media:
            print("  - " + item, file=sys.stderr)
        return EXIT_PREFLIGHT

    if not args.skip_preflight:
        present, missing = check_checkpoint_layout(checkpoint_dir)
        if missing:
            print("ERROR: checkpoint layout incomplete at %s" % checkpoint_dir, file=sys.stderr)
            for item in missing:
                print("  - missing " + item, file=sys.stderr)
            print("  (%d required paths present). Run download_weights.sh first." % len(present),
                  file=sys.stderr)
            return EXIT_PREFLIGHT

    resolve_executables(args)
    plan = build_plan(
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
        master_port=args.master_port,
    )

    if args.dry_run:
        print(PLAN_MARKER + json.dumps(plan))
        return EXIT_OK

    output_dir.mkdir(parents=True, exist_ok=True)

    import os
    env = dict(os.environ)
    env.update(plan["env_overrides"])

    print("[render-kit] launching: %s" % " ".join(plan["argv"]))
    started = time.time()
    proc = subprocess.run(plan["argv"], cwd=plan["cwd"], env=env)
    wall = time.time() - started

    result = {
        "exit_code": proc.returncode,
        "wall_seconds": round(wall, 3),
        "output_dir": plan["output_dir"],
        "expected_files": plan["expected_output_files"],
        "expected_output_seconds": plan["expected_output_seconds"],
    }
    print(RESULT_MARKER + json.dumps(result))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
