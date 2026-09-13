"""Scoring math + end-to-end scoring on synthetic clips."""
from __future__ import annotations

import pytest

import gate


def test_score_from_ratio_bounds():
    assert gate.score_from_ratio(0.0, 0.4) == 1.0
    assert gate.score_from_ratio(0.1, 0.4) == pytest.approx(0.75)
    assert gate.score_from_ratio(0.2, 0.4) == pytest.approx(0.5)
    assert gate.score_from_ratio(0.4, 0.4) == 0.0
    assert gate.score_from_ratio(0.9, 0.4) == 0.0  # clamped, never negative
    assert gate.score_from_ratio(0.0, 0.0) == 1.0  # degenerate max


def test_good_clip_scores_100_and_accepts(clips):
    rep = gate.evaluate(str(clips["good"]))
    assert rep["verdict"] == "accept"
    assert rep["score"] == pytest.approx(100.0)
    assert rep["codes"] == []


def test_accept_threshold_boundary_rejects(clips, write_policy):
    pol = gate.Policy.load(write_policy(thresholds={"accept_score": 100.5}))
    rep = gate.evaluate(str(clips["good"]), policy=pol)
    assert rep["verdict"] == "reject"
    assert "below_accept_score" in rep["codes"]
    assert rep["score"] == pytest.approx(100.0)  # score unaffected by decision


def test_score_basis_renormalizes_over_available(clips):
    rep = gate.evaluate(str(clips["good"]))
    basis = rep["score_basis"]
    # available: black_frames(1.0) + silence(1.0) + loudness(0.5); stubs excluded
    assert basis["available_weight"] == pytest.approx(2.5)
    assert "lip_sync_wer" not in basis["scored"]
    assert "face_sim" not in basis["scored"]


def test_report_required_keys(clips):
    rep = gate.evaluate(str(clips["good"]))
    for key in (
        "gate_id", "gate_version", "policy_id", "policy_sha256", "policy_parser", "video",
        "video_sha256", "duration_s", "evaluated_at", "probe", "checks",
        "score", "score_basis", "verdict", "codes", "reasons",
    ):
        assert key in rep, f"missing report key: {key}"
    ids = [c["id"] for c in rep["checks"]]
    assert ids == ["integrity", "black_frames", "silence", "loudness",
                   "lip_sync_wer", "face_sim"]
    assert rep["policy_sha256"] == gate.Policy.load().sha256


def test_partial_black_scores_between(clips):
    rep = gate.evaluate(str(clips["partial"]))
    assert rep["verdict"] == "accept"
    assert 0.0 < rep["checks"][1]["value"]["black_ratio"] < 0.2
    assert 80.0 <= rep["score"] <= 96.0


def test_evaluate_does_not_mutate_input_and_is_json_serializable(clips):
    import json

    rep = gate.evaluate(str(clips["good"]))
    blob = json.dumps(rep, sort_keys=True)  # must be JSON-serializable as-is
    assert '"verdict": "accept"' in blob
