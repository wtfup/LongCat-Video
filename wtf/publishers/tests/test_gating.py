"""Dark-gate tests: env unlock + per-channel approval validation.

Every combination that must NOT unlock is asserted here, including wildcard
env values, expiry, malformed/unreadable files, cross-channel isolation, and
the shipped-state guarantee that the repo's approvals/ dir unlocks nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from base import CHANNELS, evaluate_gate
from helpers import approval_doc, write_approval


def _lift(channel, tmp_path, env=None):
    """Gate with an explicit env mapping (never the real process env)."""
    return evaluate_gate(channel, approvals_dir=tmp_path, env=env or {})


# ---------------------------------------------------------------------------
# default / shipped state
# ---------------------------------------------------------------------------


def test_default_state_all_channels_blocked():
    for channel in CHANNELS:
        decision = evaluate_gate(channel, approvals_dir=Path("/nonexistent-approvals"), env={})
        assert decision.live_allowed is False
        assert "env_unlock_missing" in decision.reason
        assert "approval_missing" in decision.reason


def test_shipped_approvals_dir_unlocks_nothing():
    """Even with the env key set to the channel, the repo ships no unlock."""
    for channel in CHANNELS:
        decision = evaluate_gate(channel, env={"PUBLISH_LIVE": channel})
        assert decision.live_allowed is False
        assert decision.approval_ok is False
        assert "approval_" in decision.reason


# ---------------------------------------------------------------------------
# two-keys-required behavior
# ---------------------------------------------------------------------------


def test_env_only_does_not_unlock(tmp_path):
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.env_ok is True
    assert decision.approval_ok is False
    assert decision.live_allowed is False
    assert "approval_missing" in decision.reason


def test_approval_only_does_not_unlock(tmp_path):
    write_approval(tmp_path, "instagram")
    decision = _lift("instagram", tmp_path, env={})
    assert decision.approval_ok is True
    assert decision.env_ok is False
    assert decision.live_allowed is False
    assert "env_unlock_missing" in decision.reason


def test_env_plus_approval_unlocks(tmp_path):
    write_approval(tmp_path, "instagram")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is True
    assert decision.reason == ""
    assert decision.env_ok is True and decision.approval_ok is True
    assert "valid" in decision.detail


@pytest.mark.parametrize(
    "bad_value",
    ["all", "*", "true", "1", "yes", "Instagram", "INSTAGRAM", "insta", "instagramx", "instagram,youtube", "x,instagram"],
)
def test_wildcard_and_mismatch_env_values_never_unlock(tmp_path, bad_value):
    write_approval(tmp_path, "instagram")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": bad_value})
    assert decision.live_allowed is False
    assert "env_unlock_mismatch" in decision.reason


def test_env_value_surrounding_whitespace_is_stripped(tmp_path):
    write_approval(tmp_path, "instagram")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "  instagram  "})
    assert decision.live_allowed is True


def test_empty_env_value_is_treated_as_missing(tmp_path):
    write_approval(tmp_path, "instagram")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "   "})
    assert decision.live_allowed is False
    assert "env_unlock_missing" in decision.reason


# ---------------------------------------------------------------------------
# approval-file validation (fail closed on every deviation)
# ---------------------------------------------------------------------------


def test_expired_approval_refused(tmp_path):
    write_approval(tmp_path, "instagram", expires_in_minutes=-1)
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_expired" in decision.reason


def test_exact_expiry_boundary_refused(tmp_path):
    write_approval(tmp_path, "instagram", expires_in_minutes=0)
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_expired" in decision.reason


def test_naive_timestamp_refused(tmp_path):
    doc = approval_doc("instagram", expires_at="2030-01-01T00:00:00")
    (tmp_path / "instagram.json").write_text(json.dumps(doc), encoding="utf-8")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_bad_timestamp" in decision.reason


def test_missing_field_refused(tmp_path):
    doc = approval_doc("instagram")
    del doc["approved_by"]
    (tmp_path / "instagram.json").write_text(json.dumps(doc), encoding="utf-8")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_missing_fields" in decision.reason


def test_unknown_field_refused(tmp_path):
    doc = approval_doc("instagram", chanel="instagram")
    (tmp_path / "instagram.json").write_text(json.dumps(doc), encoding="utf-8")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_unknown_fields" in decision.reason


def test_example_flag_never_unlocks(tmp_path):
    doc = approval_doc("instagram", example=True)
    (tmp_path / "instagram.json").write_text(json.dumps(doc), encoding="utf-8")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_example_file" in decision.reason


def test_channel_mismatch_refused(tmp_path):
    doc = approval_doc("youtube")
    (tmp_path / "instagram.json").write_text(json.dumps(doc), encoding="utf-8")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_channel_mismatch" in decision.reason


def test_malformed_json_refused(tmp_path):
    (tmp_path / "instagram.json").write_text("{not json", encoding="utf-8")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_malformed" in decision.reason


def test_non_utf8_refused(tmp_path):
    (tmp_path / "instagram.json").write_bytes(b"\xff\xfe{}")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_unreadable" in decision.reason


def test_directory_named_like_approval_refused(tmp_path):
    (tmp_path / "instagram.json").mkdir()
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_not_file" in decision.reason


def test_expiry_before_approved_refused(tmp_path):
    doc = approval_doc("instagram")
    doc["approved_at"], doc["expires_at"] = doc["expires_at"], doc["approved_at"]
    (tmp_path / "instagram.json").write_text(json.dumps(doc), encoding="utf-8")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_bad_window" in decision.reason


def test_non_object_approval_refused(tmp_path):
    (tmp_path / "instagram.json").write_text("[1, 2, 3]", encoding="utf-8")
    decision = _lift("instagram", tmp_path, env={"PUBLISH_LIVE": "instagram"})
    assert decision.live_allowed is False
    assert "approval_not_object" in decision.reason


# ---------------------------------------------------------------------------
# channel isolation + unknown channels
# ---------------------------------------------------------------------------


def test_unknown_channel_refused(tmp_path):
    decision = _lift("tiktok", tmp_path, env={"PUBLISH_LIVE": "tiktok"})
    assert decision.live_allowed is False
    assert "unknown_channel" in decision.reason


def test_cross_channel_isolation(tmp_path):
    write_approval(tmp_path, "instagram")
    env = {"PUBLISH_LIVE": "instagram"}
    assert _lift("instagram", tmp_path, env=env).live_allowed is True
    for other in ("youtube", "facebook", "x"):
        decision = _lift(other, tmp_path, env=env)
        assert decision.live_allowed is False
        assert decision.approval_ok is False
        assert "approval_missing" in decision.reason


def test_one_env_value_unlocks_at_most_one_channel(tmp_path):
    """PUBLISH_LIVE holds a single channel name; a full valid set unlocks 1/4."""
    for channel in CHANNELS:
        write_approval(tmp_path, channel)
    unlocked = [c for c in CHANNELS if _lift(c, tmp_path, env={"PUBLISH_LIVE": "x"}).live_allowed]
    assert unlocked == ["x"]


def test_gate_decision_to_dict_contract(tmp_path):
    decision = _lift("instagram", tmp_path, env={})
    payload = decision.to_dict()
    assert set(payload) == {
        "channel",
        "live_allowed",
        "reason",
        "env_ok",
        "approval_ok",
        "env_unlock_value",
        "approval_path",
        "detail",
    }
    assert payload["channel"] == "instagram"
    assert payload["live_allowed"] is False
