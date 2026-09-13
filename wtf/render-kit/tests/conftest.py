"""Shared pytest fixtures for the W3 Render Kit suite (stdlib-only)."""

import json
import sys
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[1]
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

# Sampling constants mirrored from upstream run_demo_avatar_single_audio_to_video.py
# (avatar-v1.5: save_fps=25, num_frames=93, num_cond_frames=13).
NUM_FRAMES = 93
NUM_COND_FRAMES = 13
SAVE_FPS = 25


def make_fake_repo(root: Path) -> Path:
    """Create a fake WTF fork repo with a complete (empty-file) checkpoint layout."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    (root / "run_demo_avatar_single_audio_to_video.py").write_text("# upstream stub\n")

    assets = root / "assets" / "avatar" / "single"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "man.png").write_bytes(b"\x89PNG\r\n")
    (assets / "man.mp3").write_bytes(b"ID3")
    (root / "assets" / "avatar" / "single_example_1.json").write_text(json.dumps({
        "prompt": "A test avatar prompt.",
        "cond_image": "assets/avatar/single/man.png",
        "cond_audio": {"person1": "assets/avatar/single/man.mp3"},
    }))

    ckpt = root / "weights" / "LongCat-Video-Avatar-1.5"
    (ckpt / "base_model_int8").mkdir(parents=True)
    (ckpt / "base_model_int8" / "config.json").write_text("{}")
    (ckpt / "base_model_int8" / "model-00001-of-00001.safetensors").write_bytes(b"\x00" * 16)
    (ckpt / "lora").mkdir()
    (ckpt / "lora" / "dmd_lora.safetensors").write_bytes(b"\x00" * 16)
    (ckpt / "scheduler").mkdir()
    (ckpt / "scheduler" / "scheduler_config.json").write_text("{}")
    (ckpt / "whisper-large-v3").mkdir()
    (ckpt / "whisper-large-v3" / "config.json").write_text("{}")
    (ckpt / "vocal_separator").mkdir()
    (ckpt / "vocal_separator" / "Kim_Vocal_2.onnx").write_bytes(b"\x00" * 16)

    foundation = root / "weights" / "LongCat-Video"
    for sub in ("tokenizer", "text_encoder", "vae"):
        (foundation / sub).mkdir(parents=True, exist_ok=True)
        (foundation / sub / "config.json").write_text("{}")

    return root


@pytest.fixture
def fake_repo(tmp_path):
    return make_fake_repo(tmp_path / "repo")
