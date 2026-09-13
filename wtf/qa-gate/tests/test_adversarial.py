"""Adversarial inputs: injection-shaped filenames, forged reports, hostile YAML.

Local process args are list-based (never shell), so these must all behave:
weird names are handled, forged reports and hostile policy files fail closed.
"""
from __future__ import annotations

import json
import shutil

import pytest

import gate


def test_filename_with_shell_metacharacters(clips, tmp_path):
    weird = tmp_path / "weird name; rm -rf $HOME 'quoted' \"dq\" | pipe.mp4"
    shutil.copyfile(clips["good"], weird)
    rep = gate.evaluate(str(weird))
    assert rep["verdict"] == "accept"
    assert rep["score"] == pytest.approx(100.0)


def test_relative_path_resolved(clips, tmp_path, monkeypatch):
    local = tmp_path / "clip.mp4"
    shutil.copyfile(clips["good"], local)
    monkeypatch.chdir(tmp_path)
    rep = gate.evaluate("clip.mp4")
    assert rep["video"].startswith("/")  # report always carries an absolute path
    assert rep["verdict"] == "accept"


def test_forged_accept_without_score_fails_closed(make_db):
    db = make_db()
    with pytest.raises(gate.DbError) as ei:
        gate.record_result(db, "job-1", {"verdict": "accept", "score": None})
    assert "accept verdict requires a numeric score" in str(ei.value)


def test_forged_verdict_fails_closed(make_db):
    db = make_db()
    with pytest.raises(gate.DbError):
        gate.record_result(db, "job-1", {"verdict": "maybe", "score": 10})


def test_hostile_yaml_tag_fails_closed(tmp_path):
    evil = tmp_path / "evil.yaml"
    evil.write_text("!!python/object/apply:os.system ['echo pwned']\n")
    with pytest.raises(gate.PolicyError):
        gate.Policy.load(str(evil))


def test_non_dict_json_report_fails_closed(make_db):
    db = make_db()
    with pytest.raises(gate.DbError):
        gate.record_result(db, "job-1", ["not", "a", "dict"])  # type: ignore[arg-type]


def test_report_json_roundtrip_is_exact(clips, tmp_path):
    rep = gate.evaluate(str(clips["good"]))
    f = tmp_path / "r.json"
    f.write_text(json.dumps(rep, sort_keys=True))
    again = json.loads(f.read_text())
    assert again == rep
