"""Detector checks: black frames, silence, loudness."""
from __future__ import annotations

import pytest

import gate


def _check(rep, cid):
    return next(c for c in rep["checks"] if c["id"] == cid)


def test_black_clip_detected_and_rejected(clips):
    rep = gate.evaluate(str(clips["black"]))
    chk = _check(rep, "black_frames")
    assert chk["status"] == "fail"
    assert chk["value"]["black_ratio"] > 0.8
    assert "black_ratio_exceeded" in rep["codes"]
    assert rep["verdict"] == "reject"


def test_good_clip_has_no_black(clips):
    rep = gate.evaluate(str(clips["good"]))
    chk = _check(rep, "black_frames")
    assert chk["status"] == "pass"
    assert chk["value"]["black_ratio"] <= 0.02


def test_silent_clip_detected_and_rejected(clips):
    rep = gate.evaluate(str(clips["silent"]))
    chk = _check(rep, "silence")
    assert chk["status"] == "fail"
    assert chk["value"]["silence_ratio"] > 0.8
    assert "silence_ratio_exceeded" in rep["codes"]
    assert rep["verdict"] == "reject"


def test_good_clip_not_silent(clips):
    rep = gate.evaluate(str(clips["good"]))
    chk = _check(rep, "silence")
    assert chk["status"] == "pass"
    assert chk["value"]["silence_ratio"] == pytest.approx(0.0, abs=0.05)


def test_loudness_measured_on_good_clip(clips):
    rep = gate.evaluate(str(clips["good"]))
    chk = _check(rep, "loudness")
    assert chk["status"] == "pass"
    assert -45.0 < chk["value"]["mean_volume_dbfs"] < -10.0


def test_loudness_floor_flags_dead_audio(clips):
    # silent clip: mean volume ~ -91 dBFS -> below the -45 dBFS floor
    rep = gate.evaluate(str(clips["silent"]))
    chk = _check(rep, "loudness")
    assert chk["status"] == "fail"
    assert "loudness_below_floor" in rep["codes"]


def test_partial_black_is_measurable(clips):
    rep = gate.evaluate(str(clips["partial"]))
    chk = _check(rep, "black_frames")
    assert chk["status"] == "pass"
    assert 0.0 < chk["value"]["black_ratio"] < 0.2
    assert chk["value"]["max_black_ratio"] == pytest.approx(0.40)
