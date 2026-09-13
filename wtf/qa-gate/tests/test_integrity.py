"""Integrity checks: probe, streams, duration, decode scan — fail closed."""
from __future__ import annotations

import pytest

import gate


def _integrity(rep):
    return next(c for c in rep["checks"] if c["id"] == "integrity")


def test_good_clip_probe_ok(clips):
    rep = gate.evaluate(str(clips["good"]))
    chk = _integrity(rep)
    assert chk["status"] == "pass"
    assert chk["value"]["probe_ok"] is True
    assert chk["value"]["has_video"] is True
    assert chk["value"]["has_audio"] is True
    assert chk["value"]["decode_errors"] == 0
    assert rep["duration_s"] == pytest.approx(3.0, abs=0.3)


def test_corrupt_file_rejected(clips):
    rep = gate.evaluate(str(clips["corrupt"]))
    assert rep["verdict"] == "reject"
    assert "probe_failed" in rep["codes"]
    assert _integrity(rep)["status"] == "fail"


def test_missing_file_rejected(tmp_path):
    rep = gate.evaluate(str(tmp_path / "nope.mp4"))
    assert rep["verdict"] == "reject"
    assert "file_not_found" in rep["codes"]


def test_no_audio_stream_rejected(clips):
    rep = gate.evaluate(str(clips["noaudio"]))
    assert rep["verdict"] == "reject"
    assert "no_audio_stream" in rep["codes"]
    chk = _integrity(rep)
    assert chk["status"] == "fail"
    assert chk["value"]["has_audio"] is False


def test_no_audio_allowed_when_policy_permits(clips, write_policy):
    pol = gate.Policy.load(write_policy(thresholds={"require_audio": False}))
    rep = gate.evaluate(str(clips["noaudio"]), policy=pol)
    assert rep["verdict"] == "accept"
    sil = next(c for c in rep["checks"] if c["id"] == "silence")
    assert sil["status"] == "skipped"  # not applicable: policy waived the audio requirement


def test_short_clip_rejected(clips):
    rep = gate.evaluate(str(clips["short"]))
    assert rep["verdict"] == "reject"
    assert "duration_below_min" in rep["codes"]


def test_expected_duration_mismatch_rejected(clips):
    rep = gate.evaluate(str(clips["good"]), expect_duration=10.0)
    assert rep["verdict"] == "reject"
    assert "duration_mismatch" in rep["codes"]


def test_expected_duration_within_tolerance_accepts(clips):
    rep = gate.evaluate(str(clips["good"]), expect_duration=3.1)
    assert rep["verdict"] == "accept"
    assert not any("duration" in c for c in rep["codes"])


def test_duration_above_max_rejected(clips, write_policy):
    pol = gate.Policy.load(write_policy(thresholds={"max_duration_s": 2.0}))
    rep = gate.evaluate(str(clips["good"]), policy=pol)
    assert rep["verdict"] == "reject"
    assert "duration_above_max" in rep["codes"]


def test_video_sha256_present_and_stable(clips):
    a = gate.evaluate(str(clips["good"]))
    b = gate.evaluate(str(clips["good"]))
    assert len(a["video_sha256"]) == 64
    assert a["video_sha256"] == b["video_sha256"]
