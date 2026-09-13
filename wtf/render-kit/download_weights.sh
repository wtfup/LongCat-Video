#!/usr/bin/env bash
# =============================================================================
# WTF Avatar Factory — W3 Render Kit
# Download the exact LongCat-Video-Avatar 1.5 weights (and the shared
# foundation model) from HuggingFace into the layout the upstream demo loads.
#
# RUNS ON THE BOX ONLY (needs network + ~enough disk; the pilot volume is
# 300 GB gp3). Handles no secrets — both repos are public (MIT weights).
#
# Exact HF ids (WTF fork README.md, "Model Download"):
#   meituan-longcat/LongCat-Video             -> $WEIGHTS_DIR/LongCat-Video
#   meituan-longcat/LongCat-Video-Avatar-1.5  -> $WEIGHTS_DIR/LongCat-Video-Avatar-1.5
# The upstream demo resolves tokenizer/text_encoder/vae from
# <checkpoint_dir>/../LongCat-Video, so the directory NAMES matter.
#
# Usage:
#   bash download_weights.sh [--repo-dir DIR] [--weights-dir DIR]
#                            [--revision REF] [--fast] [--verify-only] [--hash-all]
# =============================================================================
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/wtf-avatar-factory}"
WEIGHTS_DIR="${WEIGHTS_DIR:-}"
REVISION=""
FAST=0
VERIFY_ONLY=0
HASH_ALL=0

FOUNDATION_ID="meituan-longcat/LongCat-Video"
AVATAR_ID="meituan-longcat/LongCat-Video-Avatar-1.5"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo-dir) REPO_DIR="$2"; shift 2 ;;
    --weights-dir) WEIGHTS_DIR="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --fast) FAST=1; shift ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    --hash-all) HASH_ALL=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -n "$WEIGHTS_DIR" ] || WEIGHTS_DIR="$REPO_DIR/weights"

step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

hash_file() {
  # sha256 of one file; Linux sha256sum, macOS shasum fallback
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# ---------------------------------------------------------------------------
# Layout verification — keep in sync with run_avatar.py:check_checkpoint_layout
# ---------------------------------------------------------------------------
verify_layout() {
  local base="$1" missing=0
  local ckpt="$base/LongCat-Video-Avatar-1.5"
  local found="$base/LongCat-Video"

  require_file() {
    if [ -f "$1" ]; then echo "  ok   $1"; else echo "  MISS $1   ($2)"; missing=1; fi
  }
  require_dir() {
    if [ -d "$1" ] && [ -n "$(ls -A "$1" 2>/dev/null)" ]; then
      echo "  ok   $1"
    else
      echo "  MISS $1   ($2)"; missing=1
    fi
  }

  echo "-- avatar checkpoint: $ckpt"
  require_file "$ckpt/base_model_int8/config.json"                "int8 DiT config (--use_int8)"
  if [ -f "$ckpt/base_model_int8/quantized_model.safetensors.index.json" ]; then
    echo "  ok   $ckpt/base_model_int8/quantized_model.safetensors.index.json"
  elif ls "$ckpt"/base_model_int8/*.safetensors >/dev/null 2>&1; then
    echo "  ok   $ckpt/base_model_int8/*.safetensors (single-file fallback)"
  else
    echo "  MISS $ckpt/base_model_int8/*.safetensors (int8 DiT weights; --use_int8)"; missing=1
  fi
  require_file "$ckpt/lora/dmd_lora.safetensors"                 "distill LoRA (--use_distill, required for v1.5)"
  require_dir  "$ckpt/scheduler"                                 "v1.5 scheduler"
  require_dir  "$ckpt/whisper-large-v3"                          "Whisper-large-v3 audio encoder (v1.5)"
  require_file "$ckpt/vocal_separator/Kim_Vocal_2.onnx"          "vocal separator (upstream line 147)"

  echo "-- shared foundation: $found"
  require_dir "$found/tokenizer"     "UMT5 tokenizer"
  require_dir "$found/text_encoder"  "UMT5 text encoder"
  require_dir "$found/vae"           "Wan VAE"

  return "$missing"
}

write_manifest() {
  local base="$1" manifest="$2"
  {
    echo "wtf-avatar-factory render-kit weights manifest"
    echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host: $(hostname)"
    echo "foundation_id: $FOUNDATION_ID"
    echo "avatar_id: $AVATAR_ID"
    echo "revision: ${REVISION:-main}"
    echo
    echo "== sizes (du -sh) =="
    du -sh "$base/LongCat-Video" "$base/LongCat-Video-Avatar-1.5" 2>/dev/null || true
    echo
    echo "== small-file sha256 (configs / index / onnx) =="
    find "$base" -type f \( -name "*.json" -o -name "*.onnx" \) -print0 2>/dev/null \
      | sort -z | while IFS= read -r -d '' f; do printf '%s  %s\n' "$(hash_file "$f")" "$f"; done
    if [ "$HASH_ALL" -eq 1 ]; then
      echo
      echo "== full sha256 (--hash-all) =="
      find "$base" -type f -print0 2>/dev/null | sort -z \
        | while IFS= read -r -d '' f; do printf '%s  %s\n' "$(hash_file "$f")" "$f"; done
    fi
  } > "$manifest"
  step "Wrote manifest: $manifest"
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
if [ "$VERIFY_ONLY" -eq 0 ]; then
  HF_CLI=""
  if command -v hf >/dev/null 2>&1; then HF_CLI="hf"
  elif command -v huggingface-cli >/dev/null 2>&1; then HF_CLI="huggingface-cli"
  fi
  [ -n "$HF_CLI" ] || die "no HuggingFace CLI found — run: pip install \"huggingface_hub[cli]\""
  step "Using $HF_CLI"

  [ "$FAST" -eq 1 ] && export HF_HUB_ENABLE_HF_TRANSFER=1

  mkdir -p "$WEIGHTS_DIR"
  REV_ARGS=()
  [ -n "$REVISION" ] && REV_ARGS=(--revision "$REVISION")

  step "Downloading foundation model -> $WEIGHTS_DIR/LongCat-Video"
  "$HF_CLI" download "$FOUNDATION_ID" --local-dir "$WEIGHTS_DIR/LongCat-Video" "${REV_ARGS[@]}"

  step "Downloading avatar 1.5 model -> $WEIGHTS_DIR/LongCat-Video-Avatar-1.5"
  "$HF_CLI" download "$AVATAR_ID" --local-dir "$WEIGHTS_DIR/LongCat-Video-Avatar-1.5" "${REV_ARGS[@]}"
fi

step "Verifying expected layout under $WEIGHTS_DIR"
if verify_layout "$WEIGHTS_DIR"; then
  step "Layout OK — all required paths present"
else
  warn "Layout INCOMPLETE — the missing paths above are read by upstream code:"
  warn "  run_demo_avatar_single_audio_to_video.py (int8 DiT, dmd LoRA, whisper,"
  warn "  scheduler, vocal separator) and checkpoint_dir/../LongCat-Video for the"
  warn "  tokenizer/text_encoder/vae. If the HF repo layout differs, inspect the repo"
  warn "  file list and record the exact deviation in evidence/ — do NOT run renders"
  warn "  against a partial layout (run_avatar.py will refuse anyway)."
  exit 1
fi

MANIFEST="$WEIGHTS_DIR/download_manifest.txt"
write_manifest "$WEIGHTS_DIR" "$MANIFEST"

step "Done. Next: python run_avatar.py --dry-run"
