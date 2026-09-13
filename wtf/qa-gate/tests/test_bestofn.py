"""Best-of-N selector: pick the best eligible candidate, fail closed."""
from __future__ import annotations

import pytest

import gate


def test_winner_is_not_the_first_candidate(clips):
    sel = gate.select_best_of_n([str(clips["black"]), str(clips["good"])])
    assert sel["winner"] == str(clips["good"])
    assert sel["winner_index"] == 1
    by_path = {c["path"]: c for c in sel["candidates"]}
    assert by_path[str(clips["black"])]["eligible"] is False


def test_all_ineligible_yields_no_winner(clips):
    sel = gate.select_best_of_n(
        [str(clips["black"]), str(clips["silent"]), str(clips["corrupt"])]
    )
    assert sel["winner"] is None
    assert sel["winner_index"] is None
    assert sel["reason"] == "no_eligible_candidate"


def test_tie_breaks_to_earliest_index(clips):
    sel = gate.select_best_of_n([str(clips["good"]), str(clips["good2"])])
    assert sel["winner"] == str(clips["good"])
    assert sel["winner_index"] == 0


def test_order_independence_of_relative_quality(clips):
    a = gate.select_best_of_n([str(clips["good"]), str(clips["partial"])])
    b = gate.select_best_of_n([str(clips["partial"]), str(clips["good"])])
    assert a["winner"] == b["winner"] == str(clips["good"])
    assert a["winner_index"] == 0 and b["winner_index"] == 1


def test_ineligible_high_scorer_still_loses(clips):
    # partial(eligible ~92) vs black(ineligible, low score): partial must win
    sel = gate.select_best_of_n([str(clips["black"]), str(clips["partial"])])
    assert sel["winner"] == str(clips["partial"])


def test_expect_duration_gates_every_candidate(clips):
    sel = gate.select_best_of_n([str(clips["good"])], expect_duration=30.0)
    assert sel["winner"] is None
    assert sel["reason"] == "no_eligible_candidate"


def test_selector_reports_policy_and_scores(clips):
    sel = gate.select_best_of_n([str(clips["good"])])
    assert sel["policy_id"] == gate.Policy.load().data["policy_id"]
    cand = sel["candidates"][0]
    assert cand["verdict"] == "accept"
    assert cand["score"] == pytest.approx(100.0)
    assert set(cand) >= {"path", "verdict", "score", "eligible", "codes"}
