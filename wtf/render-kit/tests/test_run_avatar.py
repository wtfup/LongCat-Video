"""W3 Render Kit — run_avatar.py behavior tests (stdlib + pytest only, no torch)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import run_avatar as ra
from conftest import make_fake_repo

PKG_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Pure arithmetic / naming (must match upstream constants)
# ---------------------------------------------------------------------------

def test_sampling_constants_match_upstream():
    # run_demo_avatar_single_audio_to_video.py (avatar-v1.5): save_fps=25,
    # num_frames=93, num_cond_frames=13.
    assert (ra.NUM_FRAMES, ra.NUM_COND_FRAMES, ra.SAVE_FPS) == (93, 13, 25)


def test_expected_output_seconds():
    assert ra.expected_output_seconds(1) == pytest.approx(93 / 25)
    assert ra.expected_output_seconds(3) == pytest.approx(253 / 25)
    assert ra.expected_output_seconds(0) == pytest.approx(93 / 25)  # clamped to 1


def test_expected_output_files_and_final():
    assert ra.expected_output_files("ai2v", 1) == ["ai2v_demo_1.mp4"]
    files = ra.expected_output_files("ai2v", 3)
    assert files == ["ai2v_demo_1.mp4", "video_continue_2.mp4", "video_continue_3.mp4"]
    assert ra.final_output_file("ai2v", 3) == "video_continue_3.mp4"


# ---------------------------------------------------------------------------
# Input JSON validation (fail closed)
# ---------------------------------------------------------------------------

def test_input_json_validation_valid(fake_repo):
    data, errors = ra.load_and_validate_input_json(
        fake_repo / "assets/avatar/single_example_1.json", "ai2v")
    assert errors == []
    assert data["prompt"]


def test_input_json_validation_failures(fake_repo, tmp_path):
    good = json.loads((fake_repo / "assets/avatar/single_example_1.json").read_text())

    bad = dict(good, prompt="  ")
    path = tmp_path / "a.json"
    path.write_text(json.dumps(bad))
    _, errors = ra.load_and_validate_input_json(path, "ai2v")
    assert any("prompt" in e for e in errors)

    bad2 = {k: v for k, v in good.items() if k != "cond_audio"}
    path.write_text(json.dumps(bad2))
    _, errors = ra.load_and_validate_input_json(path, "ai2v")
    assert any("cond_audio.person1" in e for e in errors)

    bad3 = dict(good)
    bad3.pop("cond_image")
    path.write_text(json.dumps(bad3))
    _, errors = ra.load_and_validate_input_json(path, "ai2v")
    assert any("cond_image" in e for e in errors)
    _, errors = ra.load_and_validate_input_json(path, "at2v")  # at2v needs no image
    assert errors == []

    path.write_text("{not json")
    _, errors = ra.load_and_validate_input_json(path, "ai2v")
    assert errors and "unreadable" in errors[0]


def test_referenced_files_resolve_against_repo_root(fake_repo):
    data, _ = ra.load_and_validate_input_json(
        fake_repo / "assets/avatar/single_example_1.json", "ai2v")
    assert ra.check_referenced_files(data, fake_repo, "ai2v") == []

    (fake_repo / "assets/avatar/single/man.mp3").unlink()
    missing = ra.check_referenced_files(data, fake_repo, "ai2v")
    assert len(missing) == 1 and "man.mp3" in missing[0]


# ---------------------------------------------------------------------------
# Checkpoint layout verification (fail closed)
# ---------------------------------------------------------------------------

def test_checkpoint_layout_complete(fake_repo):
    ckpt = fake_repo / "weights/LongCat-Video-Avatar-1.5"
    present, missing = ra.check_checkpoint_layout(ckpt)
    assert missing == [], missing
    assert len(present) >= 9


def test_checkpoint_layout_reports_exact_missing_paths(fake_repo):
    ckpt = fake_repo / "weights/LongCat-Video-Avatar-1.5"
    (ckpt / "lora/dmd_lora.safetensors").unlink()
    shutil.rmtree(ckpt / "whisper-large-v3")
    present, missing = ra.check_checkpoint_layout(ckpt)
    assert any("dmd_lora.safetensors" in m for m in missing)
    assert any("whisper-large-v3" in m for m in missing)
    assert len(missing) == 2


def test_checkpoint_layout_accepts_index_json(fake_repo):
    ckpt = fake_repo / "weights/LongCat-Video-Avatar-1.5"
    for shard in (ckpt / "base_model_int8").glob("*.safetensors"):
        shard.unlink()
    (ckpt / "base_model_int8" / "quantized_model.safetensors.index.json").write_text("{}")
    _, missing = ra.check_checkpoint_layout(ckpt)
    assert missing == []


# ---------------------------------------------------------------------------
# Command planning (the locked L40S profile)
# ---------------------------------------------------------------------------

def _plan(fake_repo, launcher="torchrun", python_exe="/usr/bin/python3", torchrun_exe="/venv/bin/torchrun"):
    return ra.build_plan(
        repo_root=fake_repo,
        checkpoint_dir=fake_repo / "weights/LongCat-Video-Avatar-1.5",
        input_json=fake_repo / "assets/avatar/single_example_1.json",
        output_dir=fake_repo / "outputs_avatar_single",
        stage_1="ai2v",
        num_segments=3,
        resolution="480p",
        ref_img_index=10,
        mask_frame_range=3,
        launcher=launcher,
        python_exe=python_exe,
        torchrun_exe=torchrun_exe,
        master_port=29513,
    )


def test_build_plan_torchrun_locked_profile(fake_repo):
    plan = _plan(fake_repo)
    argv = plan["argv"]
    assert argv[0] == "/venv/bin/torchrun"
    assert argv[1:4] == ["--standalone", "--nnodes=1", "--nproc_per_node=1"]
    assert argv[4] == str(fake_repo / ra.UPSTREAM_SCRIPT)
    joined = " ".join(argv)
    assert "--use_int8" in joined and "--use_distill" in joined
    assert "--model_type avatar-v1.5" in joined
    assert "--context_parallel_size 1" in joined
    assert "--num_segments 3" in joined
    assert plan["env_overrides"] == {}
    assert plan["cwd"] == str(fake_repo)
    assert plan["final_output_file"] == "video_continue_3.mp4"
    assert plan["expected_output_seconds"] == pytest.approx(10.12)


def test_build_plan_direct_launch_sets_rendezvous_env(fake_repo):
    plan = _plan(fake_repo, launcher="direct", python_exe="/venv/bin/python")
    assert plan["argv"][0] == "/venv/bin/python"
    assert plan["argv"][1] == str(fake_repo / ra.UPSTREAM_SCRIPT)
    assert plan["env_overrides"]["RANK"] == "0"
    assert plan["env_overrides"]["WORLD_SIZE"] == "1"
    assert plan["env_overrides"]["MASTER_PORT"] == "29513"


# ---------------------------------------------------------------------------
# main() behavior
# ---------------------------------------------------------------------------

def test_dry_run_prints_plan_and_never_executes(fake_repo, capsys, monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - only hit on regression
        raise AssertionError("dry-run must not execute anything")

    monkeypatch.setattr(ra.subprocess, "run", _boom)
    rc = ra.main([
        "--repo-root", str(fake_repo),
        "--input-json", "assets/avatar/single_example_1.json",
        "--dry-run",
    ])
    assert rc == ra.EXIT_OK
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith(ra.PLAN_MARKER))
    plan = json.loads(line[len(ra.PLAN_MARKER):])
    assert plan["checkpoint_dir"] == str(fake_repo / "weights/LongCat-Video-Avatar-1.5")
    assert plan["stage_1"] == "ai2v"


def test_preflight_fails_closed_without_weights(fake_repo, capsys, monkeypatch):
    shutil.rmtree(fake_repo / "weights")

    def _boom(*a, **k):  # pragma: no cover - only hit on regression
        raise AssertionError("must not execute with incomplete layout")

    monkeypatch.setattr(ra.subprocess, "run", _boom)
    rc = ra.main(["--repo-root", str(fake_repo),
                  "--input-json", "assets/avatar/single_example_1.json"])
    assert rc == ra.EXIT_PREFLIGHT
    assert "checkpoint layout incomplete" in capsys.readouterr().err


def test_preflight_rejects_missing_input_json(fake_repo, capsys):
    rc = ra.main(["--repo-root", str(fake_repo), "--input-json", "assets/avatar/nope.json"])
    assert rc == ra.EXIT_PREFLIGHT
    assert "input json" in capsys.readouterr().err


def test_preflight_rejects_bad_num_segments(fake_repo, capsys):
    rc = ra.main(["--repo-root", str(fake_repo), "--num-segments", "0"])
    assert rc == ra.EXIT_PREFLIGHT
    assert "num-segments" in capsys.readouterr().err


def test_make_input_json(tmp_path, capsys):
    out = tmp_path / "job.json"
    rc = ra.main(["--make-input-json", str(out), "--prompt", "hello world",
                  "--audio", "voice.wav", "--image", "face.png", "--stage-1", "ai2v"])
    assert rc == ra.EXIT_OK
    payload = json.loads(out.read_text())
    assert payload == {
        "prompt": "hello world",
        "cond_audio": {"person1": "voice.wav"},
        "cond_image": "face.png",
    }
    assert capsys.readouterr().out.startswith(ra.INPUT_JSON_MARKER)

    rc = ra.main(["--make-input-json", str(tmp_path / "b.json"), "--prompt", "x",
                  "--audio", "a.wav", "--stage-1", "ai2v"])
    assert rc == ra.EXIT_PREFLIGHT


def test_module_is_stdlib_only_no_torch_import():
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import run_avatar; "
        "bad = [m for m in ('torch', 'torchvision', 'diffusers') if m in sys.modules]; "
        "assert not bad, bad; print('STDLIB_ONLY_OK')"
    ) % str(PKG_ROOT)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "STDLIB_ONLY_OK" in proc.stdout
