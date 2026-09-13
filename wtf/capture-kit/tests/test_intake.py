"""W6 Capture Kit — behavioral tests for intake.py.

Everything here is local-only: no network, no external APIs. Where ffprobe /
ffmpeg are needed (real media validation) the tests skip cleanly if the
binaries are absent, so the suite stays green on minimal boxes while still
proving the real path when the tools exist.

Run from the package dir:  python -m pytest
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

import intake
from intake import classify, organize, main

FFPROBE = shutil.which("ffprobe")
FFMPEG = shutil.which("ffmpeg")

# ---------------------------------------------------------------- fixtures


def _write(path: Path, payload: bytes = b"x" * 64) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _src(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    return raw


def _make_wav(path: Path, seconds: float = 1.0, rate: int = 48000, channels: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return path


def _make_tiny_mp4(path: Path) -> Path:
    """Real 1s 320x240 @10fps video via ffmpeg (skipped if ffmpeg missing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=10", "-t", "1.0",
         "-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


# ---------------------------------------------------------------- classification


@pytest.mark.parametrize(
    "name,bucket,content,outfit,angle,take",
    [
        ("WTF_20260914_o1_f_neutral_t1.mp4", "refs", "neutral", "o1", "front", 1),
        ("WTF_20260914_o1_s_neutral_t2.mp4", "refs", "neutral", "o1", "side", 2),
        ("WTF_20260914_o2_f_hindi_mono_t1.mp4", "face_clips", "hindi_mono", "o2", "front", 1),
        ("WTF_20260914_o2_f_english_mono_t1.mp4", "face_clips", "english_mono", "o2", "front", 1),
        ("WTF_20260914_o3_f_casual_t1.mov", "face_clips", "casual", "o3", "front", 1),
        ("WTF_20260914_o1_f_still_01.jpg", "refs", "still", "o1", "front", None),
        ("wtf_20260914_outfit2_side_casual_take3.MP4", "face_clips", "casual", "o2", "side", 3),
    ],
)
def test_classify_routes_by_token(name, bucket, content, outfit, angle, take):
    c = classify(name)
    assert c["action"] == "ingest"
    assert c["bucket"] == bucket
    assert c["content"] == content
    assert c["outfit"] == outfit
    assert c["angle"] == angle
    assert c["take"] == take
    assert c["classified_by"] == "token"


def test_classify_audio_extension_wins_over_video_content_token():
    c = classify("WTF_20260914_o1_f_hindi_mono_t1.wav")
    assert c["action"] == "ingest"
    assert c["bucket"] == "voice"
    assert c["content"] == "hindi_mono"


def test_classify_extension_fallback_and_unrecognized_skip():
    assert classify("random_take.mp4")["bucket"] == "face_clips"
    assert classify("random_take.mp4")["classified_by"] == "extension-fallback"
    assert classify("random_take.jpg")["bucket"] == "refs"
    assert classify("random_take.wav")["bucket"] == "voice"

    unknown = classify("session_notes.pdf")
    assert unknown["action"] == "skip"
    assert unknown["reason"] == "unrecognized-extension"


def test_classify_system_junk_is_ignored():
    for junk in (".DS_Store", "._WTF_20260914_o1_f_neutral_t1.mp4", "Thumbs.db"):
        c = classify(junk)
        assert c["action"] == "ignore", junk
        assert c["reason"] == "system-junk"


# ---------------------------------------------------------------- dry-run purity


def test_dry_run_touches_nothing(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_neutral_t1.mp4")
    _write(raw / "WTF_20260914_o1_f_hindi_mono_t1.wav")
    dest = tmp_path / "assets"

    before = sorted(p.name for p in raw.iterdir())
    res = organize(raw, dest, dry_run=True, validate=False)

    assert res["exit_code"] == 0
    assert not dest.exists(), "dry-run must not create the destination"
    assert sorted(p.name for p in raw.iterdir()) == before, "dry-run must not touch sources"
    assert len(res["files"]) == 2
    planned = {f["dst"] for f in res["files"]}
    assert str(dest / "assets" / "refs" / "WTF_20260914_o1_f_neutral_t1.mp4") in planned
    assert str(dest / "assets" / "voice" / "WTF_20260914_o1_f_hindi_mono_t1.wav") in planned


# ---------------------------------------------------------------- real run


def test_real_run_copies_writes_manifest_and_preserves_sources(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_hindi_mono_t1.mp4", b"video-bytes")
    _write(raw / "WTF_20260914_o1_f_still_01.jpg", b"jpg-bytes")
    _write(raw / "WTF_20260914_o1_f_casual_t1.wav", b"wav-bytes")
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=False)

    assert res["exit_code"] == 0
    assert (dest / "assets" / "face_clips" / "WTF_20260914_o1_f_hindi_mono_t1.mp4").read_bytes() == b"video-bytes"
    assert (dest / "assets" / "refs" / "WTF_20260914_o1_f_still_01.jpg").read_bytes() == b"jpg-bytes"
    assert (dest / "assets" / "voice" / "WTF_20260914_o1_f_casual_t1.wav").read_bytes() == b"wav-bytes"

    manifest_path = dest / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema"] == "wtf.capture-kit.manifest/v1"
    assert manifest["summary"]["ingested"] == 3
    assert manifest["summary"]["by_bucket"] == {"face_clips": 1, "refs": 1, "voice": 1}
    for f in manifest["files"]:
        assert len(f["sha256"]) == 64

    # copy mode: camera originals stay intact
    assert sorted(p.name for p in raw.iterdir()) == [
        "WTF_20260914_o1_f_casual_t1.wav",
        "WTF_20260914_o1_f_hindi_mono_t1.mp4",
        "WTF_20260914_o1_f_still_01.jpg",
    ]


def test_second_run_dedupes_identical_content(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_neutral_t1.mp4", b"same-bytes")
    dest = tmp_path / "assets"

    assert organize(raw, dest, validate=False)["exit_code"] == 0
    res2 = organize(raw, dest, validate=False)

    assert res2["exit_code"] == 0
    assert res2["summary"]["deduped"] == 1
    assert res2["summary"]["ingested"] == 0
    dups = list((dest / "assets").rglob("*__dup*"))
    assert dups == [], f"identical content must dedupe, got {dups}"


def test_name_collision_with_different_content_renames(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_neutral_t1.mp4", b"new-bytes")
    dest = tmp_path / "assets"
    _write(dest / "assets" / "refs" / "WTF_20260914_o1_f_neutral_t1.mp4", b"old-bytes")

    res = organize(raw, dest, validate=False)

    assert res["exit_code"] == 0
    dup = dest / "assets" / "refs" / "WTF_20260914_o1_f_neutral_t1__dup2.mp4"
    assert dup.read_bytes() == b"new-bytes"
    assert (dest / "assets" / "refs" / "WTF_20260914_o1_f_neutral_t1.mp4").read_bytes() == b"old-bytes"
    ids = {w["check_id"] for w in res["warnings"]}
    assert "name-collision-renamed" in ids


def test_zero_byte_file_is_an_error_and_not_copied(tmp_path):
    raw = _src(tmp_path)
    (raw / "WTF_20260914_o1_f_neutral_t1.mp4").write_bytes(b"")
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=False)

    assert res["exit_code"] == 1
    assert res["errors"] and res["errors"][0]["reason"] == "zero-bytes"
    assert not (dest / "assets" / "refs").exists()


def test_move_mode_removes_sources(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_still_01.jpg", b"img")
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=False, move=True)

    assert res["exit_code"] == 0
    assert not (raw / "WTF_20260914_o1_f_still_01.jpg").exists()
    assert (dest / "assets" / "refs" / "WTF_20260914_o1_f_still_01.jpg").exists()


def test_no_hash_mode_still_copies(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_still_01.jpg", b"img")
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=False, hash_files=False)
    assert res["exit_code"] == 0
    assert res["files"][0]["sha256"] is None
    assert (dest / "assets" / "refs" / "WTF_20260914_o1_f_still_01.jpg").exists()


# ---------------------------------------------------------------- validation


def test_missing_ffprobe_fails_closed_before_any_write(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_neutral_t1.mp4", b"bytes")
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=True, ffprobe_bin="/nonexistent/ffprobe-xyz")

    assert res["exit_code"] == 2
    assert any(e["reason"] == "ffprobe-not-found" for e in res["errors"])
    assert not dest.exists(), "must fail closed before creating dest"


def test_probe_injection_warns_on_short_clip_and_low_resolution(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_neutral_t1.mp4", b"bytes")
    dest = tmp_path / "assets"

    def good_probe(path):
        return {"ok": True, "has_video": True, "has_audio": False, "duration_s": 15.0,
                "width": 3840, "height": 2160, "fps": 30.0}

    res_ok = organize(raw, dest, validate=True, probe_fn=good_probe)
    assert res_ok["exit_code"] == 0
    assert res_ok["warnings"] == []

    dest2 = tmp_path / "assets2"
    res_bad = organize(raw, dest2, validate=True,
                       probe_fn=lambda p: {"ok": True, "has_video": True, "has_audio": False,
                                           "duration_s": 5.0, "width": 640, "height": 480, "fps": 12.0})
    ids = {w["check_id"] for w in res_bad["warnings"]}
    assert "duration-out-of-range" in ids
    assert "resolution-below-1080p" in ids
    assert "fps-below-24" in ids
    assert res_bad["exit_code"] == 0, "warnings alone must not fail the run"


def test_probe_injection_failure_for_voice_file_is_error(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_casual_t1.wav", b"bytes")
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=True,
                   probe_fn=lambda p: {"ok": False, "error": "boom"})
    assert res["exit_code"] == 1
    assert any(e["reason"] == "probe-failed" for e in res["errors"])
    assert not (dest / "assets" / "voice").exists()


def test_source_equal_to_dest_is_rejected(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_still_01.jpg", b"img")

    res = organize(raw, raw, validate=False)
    assert res["exit_code"] == 2
    assert any(e["reason"] == "dest-inside-source" for e in res["errors"])


# ---------------------------------------------------------------- real media (local binaries)


@pytest.mark.skipif(FFPROBE is None, reason="ffprobe not installed")
def test_real_wav_probe_roundtrip(tmp_path):
    raw = _src(tmp_path)
    _make_wav(raw / "WTF_20260914_o1_f_casual_t1.wav", seconds=1.0, rate=48000)
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=True)

    assert res["exit_code"] == 0
    f = res["files"][0]
    probe = f["probe"]
    assert probe["ok"] is True
    assert probe["sample_rate"] == 48000
    assert probe["channels"] == 1
    assert abs(probe["duration_s"] - 1.0) < 0.2
    # content token 'casual' expects 300-1800s, this is 1s -> warning
    assert "duration-out-of-range" in {w["check_id"] for w in res["warnings"]}


@pytest.mark.skipif(FFPROBE is None or FFMPEG is None, reason="ffprobe/ffmpeg not installed")
def test_real_video_probe_roundtrip(tmp_path):
    raw = _src(tmp_path)
    _make_tiny_mp4(raw / "WTF_20260914_o1_f_neutral_t1.mp4")
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=True)

    assert res["exit_code"] == 0
    probe = res["files"][0]["probe"]
    assert probe["ok"] is True
    assert probe["has_video"] is True
    assert probe["width"] == 320 and probe["height"] == 240
    assert abs(probe["fps"] - 10.0) < 0.5
    ids = {w["check_id"] for w in res["warnings"]}
    assert "resolution-below-1080p" in ids
    assert "duration-out-of-range" in ids


# ---------------------------------------------------------------- CLI


def test_cli_dry_run_exit_code_and_no_side_effects(tmp_path, capsys):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_still_01.jpg", b"img")
    dest = tmp_path / "assets"

    rc = main(["--source", str(raw), "--dest", str(dest), "--dry-run", "--no-validate"])
    assert rc == 0
    assert not dest.exists()
    out = capsys.readouterr().out
    assert "DRY RUN" in out or "dry" in out.lower()


def test_cli_json_output(tmp_path, capsys):
    raw = _src(tmp_path)
    _write(raw / "WTF_20260914_o1_f_still_01.jpg", b"img")
    dest = tmp_path / "assets"

    rc = main(["--source", str(raw), "--dest", str(dest), "--dry-run", "--no-validate", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["summary"]["seen"] == 1


def test_cli_missing_source_returns_2(tmp_path):
    rc = main(["--source", str(tmp_path / "nope"), "--dest", str(tmp_path / "assets"), "--no-validate"])
    assert rc == 2


def test_cli_unknown_flag_systemexit():
    with pytest.raises(SystemExit):
        main(["--definitely-not-a-flag"])


def test_cli_self_test_is_offline_and_green():
    assert main(["--self-test"]) == 0


def test_run_level_warning_when_source_has_no_media(tmp_path):
    raw = _src(tmp_path)
    _write(raw / "notes.pdf")
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=False)
    assert res["exit_code"] == 0
    assert res["summary"]["skipped"] == 1
    assert res["summary"]["ingested"] == 0


def test_run_records_os_junk_as_ignored(tmp_path):
    raw = _src(tmp_path)
    _write(raw / ".DS_Store", b"junk")
    _write(raw / "._WTF_20260914_o1_f_neutral_t1.mp4", b"appledouble")
    _write(raw / "WTF_20260914_o1_f_still_01.jpg", b"img")
    dest = tmp_path / "assets"

    res = organize(raw, dest, validate=False)

    assert res["exit_code"] == 0
    assert res["summary"]["ignored"] == 2
    assert res["summary"]["ingested"] == 1
    assert sorted(i["reason"] for i in res["ignored"]) == ["system-junk", "system-junk"]


def test_intake_source_has_no_network_imports():
    """Zero-network guarantee: source scan for networking modules."""
    src = Path(intake.__file__).read_text()
    for forbidden in ("import socket", "import ssl", "import urllib", "import requests",
                      "import httpx", "import aiohttp", "import ftplib", "import telnetlib",
                      "http.client", "urlopen", "requests.get", "requests.post"):
        assert forbidden not in src, "forbidden network pattern in intake.py: %s" % forbidden
