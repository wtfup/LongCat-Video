#!/usr/bin/env bash
# =============================================================================
# WTF Avatar Factory — W3 Render Kit
# Provision the LongCat-Video-Avatar 1.5 runtime on the pilot box:
#   g6e.xlarge (1x L40S 48GB), DLAMI Ubuntu 22.04, SSM-only access.
#
# RUNS ON THE BOX ONLY. Idempotent. Handles no secrets.
# What it does NOT do: download model weights (download_weights.sh), touch AWS,
# or contact any API beyond the apt/pip package mirrors.
#
# Usage:
#   bash setup_l40s.sh [--repo-dir DIR] [--venv-dir DIR] [--check-only]
#                      [--skip-apt] [--skip-flash-attn] [--receipt-dir DIR]
# =============================================================================
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/wtf-avatar-factory}"
VENV_DIR="${VENV_DIR:-$HOME/.venvs/wtf-avatar}"
RECEIPT_DIR="${RECEIPT_DIR:-$HOME/.wtf-render-kit}"
CHECK_ONLY=0
SKIP_APT=0
SKIP_FLASH_ATTN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --repo-dir) REPO_DIR="$2"; shift 2 ;;
    --venv-dir) VENV_DIR="$2"; shift 2 ;;
    --receipt-dir) RECEIPT_DIR="$2"; shift 2 ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --skip-flash-attn) SKIP_FLASH_ATTN=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- 0. sanity checks --------------------------------------------------------
step "Checking host (repo at $REPO_DIR, venv at $VENV_DIR)"

[ -d "$REPO_DIR" ] || die "repo dir not found: $REPO_DIR (sync the WTF fork first)"
[ -f "$REPO_DIR/run_demo_avatar_single_audio_to_video.py" ] \
  || die "upstream avatar demo missing under $REPO_DIR — wrong --repo-dir?"
[ -f "$REPO_DIR/requirements.txt" ] || die "missing $REPO_DIR/requirements.txt"
[ -f "$REPO_DIR/requirements_avatar.txt" ] || die "missing $REPO_DIR/requirements_avatar.txt"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  die "nvidia-smi not found — use a DLAMI/GPU image with the NVIDIA driver"
fi
GPU_LINE="$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader | head -n1)"
step "GPU: $GPU_LINE"
case "$GPU_LINE" in
  *L40S*) : ;;
  *) warn "GPU is not an L40S; the locked profile (int8+distill+cp=1) targets 1x L40S 48GB" ;;
esac

# python3.10 is the upstream pin (README: conda create -n longcat-video python=3.10)
PY_BIN=""
for cand in python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [ "$ver" = "3.10" ]; then PY_BIN="$cand"; break; fi
  fi
done
if [ -z "$PY_BIN" ]; then
  die "python3.10 not found — install it (apt-get install -y python3.10 python3.10-venv) then re-run"
fi
step "Python: $PY_BIN ($($PY_BIN --version 2>&1))"

if command -v nvcc >/dev/null 2>&1; then
  step "CUDA toolkit: $(nvcc --version | tail -n1)"
else
  warn "nvcc not found — flash-attn source build will fail without the CUDA toolkit"
fi

# --- 1. system packages ------------------------------------------------------
if [ "$SKIP_APT" -eq 0 ]; then
  step "Installing system packages (apt)"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3.10-venv python3.10-dev pkg-config build-essential \
    ffmpeg libsndfile1
else
  step "Skipping apt (--skip-apt)"
fi

command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg missing — required by the upstream video writer"

# --- 2. venv -----------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  step "Creating venv at $VENV_DIR"
  "$PY_BIN" -m venv "$VENV_DIR"
else
  step "Reusing existing venv at $VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel

if [ "$CHECK_ONLY" -eq 1 ]; then
  step "check-only: verifying current environment"
  python -c "import torch; print('torch', torch.__version__, 'cuda_available', torch.cuda.is_available())" \
    || die "torch import failed in $VENV_DIR"
  step "check-only done — no packages installed"
  exit 0
fi

# --- 3. torch (exact upstream pin, README line 74) ---------------------------
step "Installing torch 2.6.0+cu124 (upstream pin)"
pip install torch==2.6.0+cu124 torchvision==0.21.0+cu124 torchaudio==2.6.0 \
  --index-url https://download.pytorch.org/whl/cu124

# --- 4. flash-attn (README lines 76-80; prebuilt wheel first, source fallback) -
if [ "$SKIP_FLASH_ATTN" -eq 0 ]; then
  step "Installing flash-attn 2.7.4.post1 (prebuilt cu12/torch2.6/cp310 wheel first)"
  pip install ninja psutil packaging
  FLASH_WHEEL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1+cu12torch2.6cxx11abiFALSE-cp310-cp310-linux_x86_64.whl"
  if pip install "$FLASH_WHEEL"; then
    echo "flash-attn: prebuilt wheel installed"
  elif pip install --no-build-isolation flash_attn==2.7.4.post1; then
    echo "flash-attn: source build (--no-build-isolation) succeeded"
  else
    warn "flash-attn build failed. The model config enables FlashAttention-2 by default;"
    warn "capture the exact compiler error, then either fix the toolchain and re-run, or"
    warn "install xformers and switch the attention backend in the model config."
  fi
else
  step "Skipping flash-attn (--skip-flash-attn)"
fi

# --- 5. upstream requirements (apt-only / unavailable entries filtered) -------
step "Installing upstream requirements.txt (apt-only libsndfile1 + tritonserverclient filtered)"
REQ_TMP="$(mktemp -d)"
grep -v -E '^[[:space:]]*(libsndfile1|tritonserverclient)([[:space:]]|==|$)' "$REPO_DIR/requirements.txt" > "$REQ_TMP/requirements.txt"
pip install -r "$REQ_TMP/requirements.txt"

step "Installing upstream requirements_avatar.txt (apt-only libsndfile1 + tritonserverclient filtered)"
grep -v -E '^[[:space:]]*(libsndfile1|tritonserverclient)([[:space:]]|==|$)' "$REPO_DIR/requirements_avatar.txt" > "$REQ_TMP/requirements_avatar.txt"
pip install -r "$REQ_TMP/requirements_avatar.txt"
rm -rf "$REQ_TMP"

step "Installing HuggingFace download tooling"
pip install "huggingface_hub[cli]" hf_transfer

# --- 6. verify ---------------------------------------------------------------
step "Verifying the runtime"
python - <<'PY'
import importlib
for mod in ("torch", "diffusers", "transformers", "librosa", "soundfile", "imageio"):
    m = importlib.import_module(mod)
    print("%-14s %s" % (mod, getattr(m, "__version__", "?")))
import torch
assert torch.cuda.is_available(), "torch.cuda.is_available() is False"
print("cuda device:", torch.cuda.get_device_name(0))
PY

mkdir -p "$RECEIPT_DIR"
{
  echo "wtf-avatar-factory render-kit setup receipt"
  echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host: $(hostname)"
  echo "repo_dir: $REPO_DIR"
  echo "venv_dir: $VENV_DIR"
  echo "gpu: $GPU_LINE"
  echo "python: $(python --version 2>&1)"
  echo "torch: $(python -c 'import torch; print(torch.__version__)')"
} > "$RECEIPT_DIR/setup_l40s.receipt.txt"
step "Wrote $RECEIPT_DIR/setup_l40s.receipt.txt"

cat <<'NEXT'

==> Next steps on this box:
  1) bash download_weights.sh            # exact HF ids -> $REPO_DIR/weights
  2) python run_avatar.py --dry-run      # offline plan check
  3) python run_avatar.py --input-json assets/avatar/single_example_1.json \
       --output-dir ./outputs_avatar_single --stage-1 ai2v --num-segments 1
  4) python benchmark.py --budget-sec-per-10s <LOCKED>   # writes bench_receipt.json
NEXT
