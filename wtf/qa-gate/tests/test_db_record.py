"""DB recording against the canonical factory schema (temp DBs only)."""
from __future__ import annotations

import json
import sqlite3

import pytest

import gate


ACCEPT = {"verdict": "accept", "score": 95.5, "policy_id": "wtf-qa-gate-v1",
          "codes": [], "reasons": []}
REJECT = {"verdict": "reject", "score": 12.0, "policy_id": "wtf-qa-gate-v1",
          "codes": ["black_ratio_exceeded"], "reasons": ["black"]}


def _read(db, job_id="job-1"):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    events = con.execute(
        "SELECT event, meta FROM events WHERE job_id=? ORDER BY id", (job_id,)
    ).fetchall()
    con.close()
    return job, events


def test_record_accept_sets_gated(make_db):
    db = make_db()
    res = gate.record_result(db, "job-1", ACCEPT)
    assert res["status"] == "gated"
    job, events = _read(db)
    assert job["status"] == "gated"
    assert job["gate_score"] == pytest.approx(95.5)
    assert json.loads(job["gate_json"])["verdict"] == "accept"
    assert [e["event"] for e in events] == ["gate_passed"]
    assert json.loads(events[0]["meta"])["score"] == pytest.approx(95.5)


def test_record_reject_sets_rejected(make_db):
    db = make_db()
    res = gate.record_result(db, "job-1", REJECT)
    assert res["status"] == "rejected"
    job, events = _read(db)
    assert job["status"] == "rejected"
    assert [e["event"] for e in events] == ["gate_rejected"]
    assert "black_ratio_exceeded" in json.loads(events[0]["meta"])["codes"]


def test_record_unknown_job_fails_closed(make_db):
    db = make_db()
    with pytest.raises(gate.DbError) as ei:
        gate.record_result(db, "ghost", ACCEPT)
    assert "unknown_job" in str(ei.value)


def test_record_blocked_state_fails_closed(make_db):
    db = make_db(status="published")
    with pytest.raises(gate.DbError) as ei:
        gate.record_result(db, "job-1", ACCEPT)
    assert "invalid_state" in str(ei.value)


def test_record_requires_existing_schema_unless_init(tmp_path):
    import sqlite3 as s

    db = str(tmp_path / "empty.db")
    s.connect(db).close()  # empty file, no tables
    with pytest.raises(gate.DbError):
        gate.record_result(db, "job-1", ACCEPT)  # no init_db -> fail closed


def test_record_init_db_creates_schema(tmp_path):
    db = str(tmp_path / "boot.db")
    with pytest.raises(gate.DbError) as ei:
        gate.record_result(db, "job-1", ACCEPT, init_db=True)  # tables created, job missing
    assert "unknown_job" in str(ei.value)
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"jobs", "events"} <= names


def test_regate_allowed_from_gated(make_db):
    db = make_db(status="gated")
    res = gate.record_result(db, "job-1", REJECT)
    assert res["status"] == "rejected"
    _, events = _read(db)
    assert [e["event"] for e in events] == ["gate_rejected"]


def test_gate_job_end_to_end(clips, make_db):
    db = make_db(render_path=str(clips["good"]))
    res = gate.gate_job(db, "job-1")
    assert res["status"] == "gated"
    assert res["report"]["verdict"] == "accept"
    job, events = _read(db)
    assert json.loads(job["gate_json"])["video_sha256"] == res["report"]["video_sha256"]
    assert [e["event"] for e in events] == ["gate_passed"]


def test_gate_job_missing_render_path_fails_closed(make_db):
    db = make_db(render_path=None)
    with pytest.raises(gate.DbError):
        gate.gate_job(db, "job-1")
