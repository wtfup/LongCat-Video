#!/usr/bin/env bash
# WTF QA Gate v1 — regenerate the full evidence pack.
# Local/dark only: ffprobe/ffmpeg + pytest. No network, no git, no AWS.
# Usage: bash /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/qa-gate/evidence/reproduce.sh
set -uo pipefail
PKG=/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/qa-gate
EV="$PKG/evidence"
DEMO="$EV/demo_media"
PY="${PY:-python}"
mkdir -p "$DEMO"
cd "$PKG"

# 0) environment receipt
{
  echo "run_at_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "package_dir: $(pwd)"
  echo "python: $($PY --version 2>&1)"
  command -v ffprobe >/dev/null 2>&1 && ffprobe -version | head -1
  command -v ffmpeg >/dev/null 2>&1 && ffmpeg -version | head -1
} > "$EV/environment.txt"

# 1) fresh full test suite
$PY -m pytest -v > "$EV/pytest_fresh.log" 2>&1
TESTS_RC=$?
tail -1 "$EV/pytest_fresh.log" >> "$EV/environment.txt"
echo "pytest_rc=$TESTS_RC" >> "$EV/environment.txt"

# 1b) fresh suite in a bare stdlib+pytest env (how a verifier may run it)
if command -v uv >/dev/null 2>&1; then
  uv run --no-project --with pytest python -m pytest > "$EV/pytest_fresh_uv_bare.log" 2>&1
  echo "pytest_uv_bare_rc=$?" >> "$EV/environment.txt"
  tail -1 "$EV/pytest_fresh_uv_bare.log" >> "$EV/environment.txt"
fi

# 1c) record which policy parser each environment uses (pyyaml vs builtin fallback)
$PY -c "import sys; sys.path.insert(0,'.'); import gate; print('policy_parser(venv):', gate.Policy.load().parser)" >> "$EV/environment.txt" 2>&1
if command -v uv >/dev/null 2>&1; then
  uv run --no-project python -c "import sys; sys.path.insert(0,'.'); import gate; print('policy_parser(bare):', gate.Policy.load().parser)" >> "$EV/environment.txt" 2>&1
fi

# 2) demo media (lavfi sources only — offline)
gen() { ffmpeg -y -hide_banner -loglevel error "$@"; }
gen -f lavfi -i "testsrc2=d=3:s=320x240:r=10" -f lavfi -i "sine=f=440:d=3" \
    -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest "$DEMO/good.mp4"
gen -f lavfi -i "color=c=black:d=3:s=320x240:r=10" -f lavfi -i "sine=f=440:d=3" \
    -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest "$DEMO/black.mp4"
gen -f lavfi -i "testsrc2=d=3:s=320x240:r=10" -f lavfi -i "anullsrc=r=44100:cl=mono" \
    -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest "$DEMO/silent.mp4"
printf 'this is not a video file\n' > "$DEMO/corrupt.mp4"

# 3) CLI transcript (reject paths exit 2 — expected)
{
  echo "\$ python gate.py selftest"
  $PY gate.py selftest; echo "exit=$?"
  echo
  echo "\$ python gate.py score --video evidence/demo_media/good.mp4 --json evidence/report_good.json"
  $PY gate.py score --video "$DEMO/good.mp4" --json "$EV/report_good.json"; echo "exit=$?"
  echo
  echo "\$ python gate.py score --video evidence/demo_media/black.mp4 --json evidence/report_black.json"
  $PY gate.py score --video "$DEMO/black.mp4" --json "$EV/report_black.json"; echo "exit=$?"
  echo
  echo "\$ python gate.py score --video evidence/demo_media/corrupt.mp4 --json evidence/report_corrupt.json"
  $PY gate.py score --video "$DEMO/corrupt.mp4" --json "$EV/report_corrupt.json"; echo "exit=$?"
  echo
  echo "\$ python gate.py bestof --candidates black,good --json evidence/selection_bestof.json"
  $PY gate.py bestof --candidates "$DEMO/black.mp4,$DEMO/good.mp4" --json "$EV/selection_bestof.json"; echo "exit=$?"
} > "$EV/cli_demo.txt" 2>&1

# 4) DB demo (isolated demo DB inside evidence/ — canonical DB untouched)
$PY "$EV/make_demo_db.py" > "$EV/db_demo.txt" 2>&1
echo "db_demo_rc=$?" >> "$EV/environment.txt"

# 5) self-audit scans
{
  echo "== secret scan over deliverables + logs (expect 0) =="
  grep -RInE "(AKIA[0-9A-Z]{16}|aws_secret|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|xox[baprs]-|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9]{20,})" \
    "$PKG/gate.py" "$PKG/policy.yaml" "$PKG/requirements.txt" "$PKG/tests" \
    "$EV/pytest_fresh.log" "$EV/cli_demo.txt" "$EV/db_demo.txt" 2>/dev/null || echo "0 hits"
  echo
  echo "== network primitive scan in gate.py + tests (expect 0) =="
  grep -RInE "(socket\.|urllib|requests\.|httpx|aiohttp|urlopen\(|http://|https://|boto3)" \
    "$PKG/gate.py" "$PKG/tests" 2>/dev/null || echo "0 hits"
  echo
  echo "== AWS/git command scan in gate.py (expect 0) =="
  grep -nE "\b(aws|git)\b" "$PKG/gate.py" 2>/dev/null || echo "0 hits"
} > "$EV/selfaudit_scan.txt" 2>&1

echo "evidence regenerated: tests_rc=$TESTS_RC (0 = all passed)"
exit 0
