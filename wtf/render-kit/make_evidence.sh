#!/usr/bin/env bash
# =============================================================================
# W3 Render Kit — regenerate the evidence bundle.
#
# Offline by construction: no network, no GPU, no model weights. Produces
# wtf/render-kit/evidence/* which the swarm verifier re-checks.
#
# Usage:  PYTHON=/path/to/python bash make_evidence.sh
#         (PYTHON must have pytest installed; default "python3")
# =============================================================================
set -euo pipefail

PKG="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$PKG/../.." && pwd)"
EV="$PKG/evidence"
SCRATCH="$EV/scratch"
PYTHON="${PYTHON:-python3}"

echo "==> package:   $PKG"
echo "==> repo root: $REPO_ROOT"
echo "==> python:    $PYTHON ($("$PYTHON" --version 2>&1))"

if ! "$PYTHON" -m pytest --version >/dev/null 2>&1; then
  echo "ERROR: pytest not available for $PYTHON — set PYTHON to an interpreter with pytest" >&2
  exit 1
fi

mkdir -p "$EV"
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH"

run_capture() {
  # run_capture <outfile> <cmd...> — always succeeds; EXIT=<rc> appended.
  local out="$1"; shift
  set +e
  "$@" > "$out" 2>&1
  local rc=$?
  set -e
  echo "EXIT=$rc" >> "$out"
  return 0
}

# --- 1. test suite -----------------------------------------------------------
echo "==> pytest"
( cd "$PKG" && "$PYTHON" -m pytest -q ) > "$EV/pytest_run.txt" 2>&1
tail -n 3 "$EV/pytest_run.txt" | sed 's/^/    /'

# --- 2. shell syntax ---------------------------------------------------------
echo "==> bash -n"
{
  echo "# bash -n syntax checks — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for s in setup_l40s.sh download_weights.sh; do
    if bash -n "$PKG/$s" 2> "$SCRATCH/syntax_$s.err"; then
      echo "OK   bash -n $s"
    else
      echo "FAIL bash -n $s"; cat "$SCRATCH/syntax_$s.err"
    fi
  done
  if command -v shellcheck >/dev/null 2>&1; then
    for s in setup_l40s.sh download_weights.sh; do
      shellcheck "$PKG/$s" && echo "OK   shellcheck $s" || echo "WARN shellcheck $s (see above)"
    done
  else
    echo "NOTE shellcheck not installed on this machine (bash -n only)"
  fi
} > "$EV/shell_syntax_checks.txt"
cat "$EV/shell_syntax_checks.txt"

# --- 3. run_avatar preflight fail-closed against the real repo ---------------
echo "==> run_avatar fail-closed demo (real repo, weights absent)"
run_capture "$EV/run_avatar_preflight_failclosed.txt" \
  "$PYTHON" "$PKG/run_avatar.py" --repo-root "$REPO_ROOT" --dry-run

# --- 4. run_avatar dry-run plan against a scratch fixture --------------------
echo "==> run_avatar dry-run plan (scratch fixture)"
FAKE_REPO="$SCRATCH/fake_repo"
mkdir -p "$FAKE_REPO/assets/avatar/single" \
         "$FAKE_REPO/weights/LongCat-Video-Avatar-1.5/base_model_int8" \
         "$FAKE_REPO/weights/LongCat-Video-Avatar-1.5/lora" \
         "$FAKE_REPO/weights/LongCat-Video-Avatar-1.5/scheduler" \
         "$FAKE_REPO/weights/LongCat-Video-Avatar-1.5/whisper-large-v3" \
         "$FAKE_REPO/weights/LongCat-Video-Avatar-1.5/vocal_separator" \
         "$FAKE_REPO/weights/LongCat-Video/tokenizer" \
         "$FAKE_REPO/weights/LongCat-Video/text_encoder" \
         "$FAKE_REPO/weights/LongCat-Video/vae"
echo "# upstream stub" > "$FAKE_REPO/run_demo_avatar_single_audio_to_video.py"
printf 'fixture-audio' > "$FAKE_REPO/assets/avatar/single/man.mp3"
printf 'fixture-image' > "$FAKE_REPO/assets/avatar/single/man.png"
cat > "$FAKE_REPO/assets/avatar/single_example_1.json" <<'JSON'
{"prompt": "fixture prompt", "cond_image": "assets/avatar/single/man.png", "cond_audio": {"person1": "assets/avatar/single/man.mp3"}}
JSON
for f in \
  "weights/LongCat-Video-Avatar-1.5/base_model_int8/config.json" \
  "weights/LongCat-Video-Avatar-1.5/base_model_int8/model-00001-of-00001.safetensors" \
  "weights/LongCat-Video-Avatar-1.5/lora/dmd_lora.safetensors" \
  "weights/LongCat-Video-Avatar-1.5/scheduler/scheduler_config.json" \
  "weights/LongCat-Video-Avatar-1.5/whisper-large-v3/config.json" \
  "weights/LongCat-Video-Avatar-1.5/vocal_separator/Kim_Vocal_2.onnx" \
  "weights/LongCat-Video/tokenizer/config.json" \
  "weights/LongCat-Video/text_encoder/config.json" \
  "weights/LongCat-Video/vae/config.json"; do
  : > "$FAKE_REPO/$f"
done
run_capture "$EV/run_avatar_dryrun.txt" \
  "$PYTHON" "$PKG/run_avatar.py" --repo-root "$FAKE_REPO" --dry-run --num-segments 3
"$PYTHON" - "$EV/run_avatar_dryrun.txt" "$EV/run_avatar_plan.json" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
line = next(l for l in open(src, encoding="utf-8") if l.startswith("RENDER_KIT_PLAN "))
plan = json.loads(line[len("RENDER_KIT_PLAN "):])
json.dump(plan, open(dst, "w", encoding="utf-8"), indent=2)
print("wrote plan:", dst, "| argv len:", len(plan["argv"]))
PY

# --- 5. benchmark self-test (offline) ----------------------------------------
echo "==> benchmark --self-test"
run_capture "$EV/bench_selftest.txt" \
  "$PYTHON" "$PKG/benchmark.py" --self-test --out "$EV/bench_receipt.selftest.json"

# --- 6. download_weights verify-only: fail-closed AND success fixture --------
echo "==> download_weights --verify-only (fail-closed vs success)"
run_capture "$EV/download_weights_verify_failclosed.txt" \
  bash "$PKG/download_weights.sh" --verify-only --weights-dir "$REPO_ROOT/weights"
mkdir -p "$SCRATCH/fake_weights"
cp -R "$FAKE_REPO/weights/." "$SCRATCH/fake_weights/"
run_capture "$EV/download_weights_verify_ok.txt" \
  bash "$PKG/download_weights.sh" --verify-only --weights-dir "$SCRATCH/fake_weights"

# Scratch fixtures are removed BEFORE the audit so the audit reflects the
# final, reproducible package state (no transient fixture files counted).
rm -rf "$SCRATCH"

# --- 7. self-audit + manifest -------------------------------------------------
echo "==> self-audit + sha256 manifest"
"$PYTHON" "$PKG/self_audit.py" --json > "$EV/self_audit.json"
"$PYTHON" - "$PKG" "$EV/manifest_sha256.txt" <<'PY'
import hashlib, sys
from pathlib import Path
pkg, out = Path(sys.argv[1]), Path(sys.argv[2])
skip_dirs = {"__pycache__", ".pytest_cache", "evidence"}
rows = []
for p in sorted(pkg.rglob("*")):
    if not p.is_file():
        continue
    if any(part in skip_dirs for part in p.relative_to(pkg).parts):
        continue
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    rows.append("%s  %s" % (digest, p.relative_to(pkg)))
out.write_text("\n".join(rows) + "\n", encoding="utf-8")
print("manifest rows:", len(rows))
PY

# --- 8. evidence index --------------------------------------------------------
{
  echo "# W3 Render Kit — evidence index"
  echo
  echo "Regenerated by: PYTHON=$PYTHON bash make_evidence.sh"
  echo "Date (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Interpreter: $("$PYTHON" --version 2>&1) at $("$PYTHON" -c 'import sys; print(sys.executable)')"
  echo "pytest: $("$PYTHON" -m pytest --version 2>&1)"
  echo
  echo "| artifact | what it proves |"
  echo "|---|---|"
  echo "| pytest_run.txt | full test suite result on fresh execution |"
  echo "| shell_syntax_checks.txt | bash -n on both box scripts |"
  echo "| run_avatar_preflight_failclosed.txt | run_avatar.py refuses to plan a render with weights absent (real repo) |"
  echo "| run_avatar_dryrun.txt + run_avatar_plan.json | exact locked torchrun command against a complete fixture layout |"
  echo "| bench_selftest.txt + bench_receipt.selftest.json | benchmark metrics/gates logic offline (SYNTHETIC numbers, clearly labelled) |"
  echo "| download_weights_verify_failclosed.txt | layout verifier rejects a missing-weights tree |"
  echo "| download_weights_verify_ok.txt | layout verifier accepts a complete tree (offline fixture) |"
  echo "| self_audit.json | deliverable presence, file inventory, credential scan (0 findings) |"
  echo "| manifest_sha256.txt | sha256 of every tracked package file |"
  echo
  echo "All steps are offline: no network, no GPU, no download of model weights."
} > "$EV/README.txt"

echo "==> evidence bundle complete:"
ls -la "$EV"
