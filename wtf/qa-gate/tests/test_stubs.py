"""Stubbed IFACEs (lip-sync WER, face-sim) + the real WER math."""
from __future__ import annotations

import pytest

import gate


def test_lipsync_stub_is_unavailable_and_never_fabricates():
    s = gate.LipSyncWERScorer()
    m = s.score("/does/not/matter.mp4")
    assert m.status == "unavailable"
    assert m.value is None
    assert "stub" in m.detail.lower()


def test_lipsync_live_mode_refused_dark():
    with pytest.raises(gate.LiveScoringDisabled):
        gate.LipSyncWERScorer(mode="live")


def test_facesim_stub_is_unavailable():
    s = gate.FaceSimScorer()
    m = s.score("/does/not/matter.mp4", refs_dir=None)
    assert m.status == "unavailable"
    assert m.value is None


def test_facesim_live_mode_refused_dark():
    with pytest.raises(gate.LiveScoringDisabled):
        gate.FaceSimScorer(mode="live")


def test_gate_report_marks_stub_checks_unavailable(clips):
    rep = gate.evaluate(str(clips["good"]))
    lip = next(c for c in rep["checks"] if c["id"] == "lip_sync_wer")
    face = next(c for c in rep["checks"] if c["id"] == "face_sim")
    assert lip["status"] == "unavailable"
    assert face["status"] == "unavailable"
    assert lip["score"] is None and face["score"] is None
    # unavailable optional checks must not cause a rejection
    assert rep["verdict"] == "accept"
    # ...but they must be visible in the report for later enablement
    assert lip["weight"] == 3.0 and face["weight"] == 3.0


def test_required_stub_check_would_reject(clips, write_policy):
    pol = gate.Policy.load(write_policy(checks={"lip_sync_wer": {"required": True}}))
    rep = gate.evaluate(str(clips["good"]), policy=pol)
    assert rep["verdict"] == "reject"
    assert "required_metric_unavailable" in rep["codes"]


def test_wer_math_exact():
    assert gate.word_error_rate("the quick brown fox", "the quick brown fox") == 0.0
    assert gate.word_error_rate("the quick brown fox", "the quick brown cat") == pytest.approx(0.25)
    assert gate.word_error_rate("HELLO, world!", "hello world") == 0.0
    assert gate.word_error_rate("", "") == 0.0
    assert gate.word_error_rate("", "extra") == 1.0
    assert gate.word_error_rate("a b c d", "a x c d") == pytest.approx(0.25)


def test_wer_unicode_hindi():
    ref = "नमस्ते दोस्तों आज हम बात करेंगे"
    assert gate.word_error_rate(ref, ref) == 0.0
    assert gate.word_error_rate(ref, "नमस्ते दोस्तों आज हम") == pytest.approx(2 / 6)
