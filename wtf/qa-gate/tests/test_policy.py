"""Policy loading + validation must fail closed (works with or without PyYAML)."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

import gate


def test_default_policy_loads():
    pol = gate.Policy.load()
    assert pol.data["policy_id"] == "wtf-qa-gate-v1"
    assert pol.data["thresholds"]["accept_score"] == 70.0
    assert pol.data["thresholds"]["require_audio"] is True
    assert len(pol.sha256) == 64
    assert pol.parser in ("pyyaml", "builtin")
    assert str(gate.DEFAULT_POLICY_PATH).endswith("policy.yaml")


def test_policy_explicit_path_matches_default(tmp_path):
    src = Path(gate.DEFAULT_POLICY_PATH)
    dst = tmp_path / "copy.yaml"
    dst.write_text(src.read_text())
    a = gate.Policy.load()
    b = gate.Policy.load(str(dst))
    assert a.sha256 == b.sha256
    assert a.data == b.data


def test_policy_missing_file_fails_closed(tmp_path):
    with pytest.raises(gate.PolicyError):
        gate.Policy.load(str(tmp_path / "nope.yaml"))


def test_policy_invalid_yaml_fails_closed(make_policy_file):
    with pytest.raises(gate.PolicyError):
        gate.Policy.load(make_policy_file(text="::: not : yaml : [\n"))


def test_policy_missing_required_key_fails_closed(make_policy_file):
    data = copy.deepcopy(gate.Policy.load().data)
    del data["thresholds"]["accept_score"]
    with pytest.raises(gate.PolicyError):
        gate.Policy.load(make_policy_file(data=data))


def test_policy_bad_type_fails_closed(make_policy_file):
    data = copy.deepcopy(gate.Policy.load().data)
    data["thresholds"]["max_black_ratio"] = "not-a-number"
    with pytest.raises(gate.PolicyError):
        gate.Policy.load(make_policy_file(data=data))


def test_builtin_parser_parses_policy_exactly():
    """The stdlib fallback must reproduce the documented policy structure."""
    text = Path(gate.DEFAULT_POLICY_PATH).read_text()
    data = gate._yaml_load_subset(text)
    assert data["version"] == 1
    assert data["policy_id"] == "wtf-qa-gate-v1"
    assert data["thresholds"]["min_duration_s"] == 1.0
    assert data["thresholds"]["require_audio"] is True
    assert data["checks"]["lip_sync_wer"] == {"required": False, "weight": 3.0, "mode": "stub"}
    assert data["detectors"]["silencedetect"]["noise_floor_db"] == -35.0


def test_builtin_parser_matches_active_parser():
    """When PyYAML is present this proves byte-level semantic equivalence."""
    text = Path(gate.DEFAULT_POLICY_PATH).read_text()
    assert gate._yaml_load_subset(text) == gate.Policy.load().data


def test_builtin_parser_rejects_unsupported_constructs():
    with pytest.raises(gate.PolicyError):
        gate._yaml_load_subset("checks:\n  - one\n")
    with pytest.raises(gate.PolicyError):
        gate._yaml_load_subset("not a mapping line\n")
