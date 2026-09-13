"""Schema-v1 contract tests: exact DDL, state machine, events, guards."""
import sqlite3
from pathlib import Path

import pytest

from factory_core.jobqueue import (
    DEFAULT_DB_PATH,
    SCHEMA_EVENTS_SQL,
    SCHEMA_JOBS_SQL,
    STATUSES,
    TRANSITIONS,
    ConcurrentUpdateError,
    FactoryDB,
    InvalidTransition,
    NotFoundError,
    ValidationError,
)


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _canonical(sql: str) -> str:
    # SQLite stores DDL in sqlite_master without the IF NOT EXISTS clause.
    return _norm(sql).replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE")


def test_default_db_path_is_the_contract_path():
    assert str(DEFAULT_DB_PATH) == (
        "/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db"
    )


def test_schema_matches_brief_exactly(tmp_path):
    db = FactoryDB(tmp_path / "factory.db")
    try:
        assert db.tables() == ["events", "jobs"]
        stored = {
            row["name"]: row["sql"]
            for row in db._conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
        }
        assert _norm(stored["jobs"]) == _canonical(SCHEMA_JOBS_SQL)
        assert _norm(stored["events"]) == _canonical(SCHEMA_EVENTS_SQL)
        # AUTOINCREMENT must survive (event ids never reused)
        assert "AUTOINCREMENT" in stored["events"].upper()
    finally:
        db.close()


def test_schema_columns_and_types(tmp_path):
    db = FactoryDB(tmp_path / "factory.db")
    try:
        jobs_cols = {r["name"]: r for r in db._conn.execute("PRAGMA table_info(jobs)")}
        assert list(jobs_cols) == [
            "id", "created_at", "brand", "pillar", "language", "title", "script",
            "status", "render_path", "gate_score", "gate_json", "notes",
        ]
        assert jobs_cols["id"]["pk"] == 1
        assert jobs_cols["gate_score"]["type"] == "REAL"
        assert jobs_cols["status"]["dflt_value"] == "'queued'"
        for required in ("created_at", "brand", "pillar", "language", "status"):
            assert jobs_cols[required]["notnull"] == 1

        event_cols = {r["name"]: r for r in db._conn.execute("PRAGMA table_info(events)")}
        assert list(event_cols) == ["id", "job_id", "ts", "event", "meta"]
        assert event_cols["id"]["pk"] == 1
        for required in ("job_id", "ts", "event"):
            assert event_cols[required]["notnull"] == 1
    finally:
        db.close()


def test_init_schema_is_idempotent(tmp_path):
    path = tmp_path / "factory.db"
    a = FactoryDB(path)
    a.close()
    b = FactoryDB(path)  # must not raise, must not duplicate
    try:
        assert b.tables() == ["events", "jobs"]
    finally:
        b.close()


def test_add_requires_core_fields(tmp_path):
    db = FactoryDB(tmp_path / "factory.db")
    try:
        for kwargs in (
            {"brand": "", "pillar": "p", "language": "l"},
            {"brand": "b", "pillar": "  ", "language": "l"},
            {"brand": "b", "pillar": "p", "language": ""},
        ):
            with pytest.raises(ValidationError):
                db.add_job(**kwargs)
        with pytest.raises(ValidationError):
            db.add_job(brand="b", pillar="p", language="l", status="bogus")
        job = db.add_job(brand="b", pillar="p", language="l", job_id="job_fixed_0001")
        assert job.status == "queued"
        with pytest.raises(ValidationError):
            db.add_job(brand="b", pillar="p", language="l", job_id="job_fixed_0001")
    finally:
        db.close()


def test_add_and_get_roundtrip(db):
    job = db.add_job(
        brand="WTF Gyms",
        pillar="fitness-transformation",
        language="hindi",
        title="T",
        script="S",
        notes="N",
    )
    assert job.id.startswith("job_")
    got = db.get_job(job.id)
    assert got == job
    assert got.created_at.endswith("+00:00")
    with pytest.raises(NotFoundError):
        db.get_job("job_missing")


def test_list_filters_status_and_limit(db):
    a = db.add_job(brand="b", pillar="p", language="l", title="first")
    b = db.add_job(brand="b", pillar="p", language="l", title="second")
    db.transition(a.id, "rendering")
    assert [j.id for j in db.list_jobs()] == [a.id, b.id]
    assert [j.id for j in db.list_jobs(status="queued")] == [b.id]
    assert [j.id for j in db.list_jobs(status="rendering")] == [a.id]
    assert [j.id for j in db.list_jobs(limit=1)] == [a.id]
    assert [j.id for j in db.list_jobs(newest_first=True, limit=1)] == [b.id]
    with pytest.raises(ValidationError):
        db.list_jobs(status="bogus")
    with pytest.raises(ValidationError):
        db.list_jobs(limit=0)


def test_fifo_order_with_identical_timestamps(tmp_path):
    """FIFO must be insertion order, not wall-clock or id order."""
    frozen = "2026-09-13T00:00:00+00:00"
    db = FactoryDB(tmp_path / "factory.db", clock=lambda: frozen)
    try:
        ids = [db.add_job(brand="b", pillar="p", language="l").id for _ in range(5)]
        assert [j.id for j in db.list_jobs()] == ids
        assert [j.id for j in db.list_jobs(newest_first=True)] == list(reversed(ids))
    finally:
        db.close()


def test_happy_path_transitions_and_events(db):
    job = db.add_job(brand="b", pillar="p", language="l")
    for nxt in ("rendering", "rendered", "gated", "approved", "published"):
        job = db.transition(job.id, nxt)
        assert job.status == nxt
    events = db.get_events(job.id)
    assert events[0]["event"] == "created"
    status_changes = [e for e in events if e["event"] == "status_change"]
    assert [e["meta"]["to"] for e in status_changes] == [
        "rendering", "rendered", "gated", "approved", "published",
    ]
    assert status_changes[0]["meta"]["from"] == "queued"


def test_illegal_transitions_fail_closed(db):
    job = db.add_job(brand="b", pillar="p", language="l")
    with pytest.raises(InvalidTransition):
        db.transition(job.id, "approved")  # queued -> approved skips stages
    assert db.get_job(job.id).status == "queued"  # unchanged
    with pytest.raises(ValidationError):
        db.transition(job.id, "not_a_status")
    db.transition(job.id, "rendering")
    with pytest.raises(InvalidTransition):
        db.transition(job.id, "queued")  # no backwards movement
    # terminal jobs cannot transition
    db.transition(job.id, "failed")
    with pytest.raises(InvalidTransition):
        db.transition(job.id, "rendering")


def test_state_machine_matches_schema_v1():
    assert TRANSITIONS["queued"] == ("rendering", "failed")
    assert TRANSITIONS["rendering"] == ("rendered", "failed")
    assert TRANSITIONS["rendered"] == ("gated", "failed")
    assert TRANSITIONS["gated"] == ("approved", "rejected", "failed")
    assert TRANSITIONS["approved"] == ("published", "failed")
    assert TRANSITIONS["rejected"] == ()
    assert TRANSITIONS["published"] == ()
    assert TRANSITIONS["failed"] == ()
    assert set(STATUSES) == {
        "queued", "rendering", "rendered", "gated",
        "approved", "rejected", "published", "failed",
    }


def test_fail_is_idempotent_and_requeue_works(db):
    job = db.add_job(brand="b", pillar="p", language="l")
    db.transition(job.id, "rendering")
    failed = db.fail_job(job.id, "render blew up")
    assert failed.status == "failed"
    again = db.fail_job(job.id, "another reason")  # idempotent
    assert again.status == "failed"
    requeued = db.requeue(job.id, reason="retry")
    assert requeued.status == "queued"
    with pytest.raises(InvalidTransition):
        db.requeue(job.id)  # only failed jobs can be requeued


def test_fail_refused_on_terminal_jobs(db):
    job = db.add_job(brand="b", pillar="p", language="l")
    for nxt in ("rendering", "rendered", "gated", "rejected"):
        job = db.transition(job.id, nxt)
    with pytest.raises(InvalidTransition):
        db.fail_job(job.id, "too late")


def test_concurrent_update_is_detected(db, tmp_path):
    job = db.add_job(brand="b", pillar="p", language="l")
    other = FactoryDB(tmp_path / "factory.db")
    try:
        stale = other.get_job(job.id)  # snapshot: queued
        db.transition(job.id, "rendering")  # another writer wins
        # Simulate the stale writer: keep reading the outdated snapshot, then write.
        other.get_job = lambda _jid: stale  # type: ignore[assignment]
        with pytest.raises(ConcurrentUpdateError):
            other.transition(job.id, "rendering")
    finally:
        other.close()


def test_update_job_field_whitelist(db):
    job = db.add_job(brand="b", pillar="p", language="l")
    updated = db.update_job(job.id, title="new title", notes="n")
    assert updated.title == "new title" and updated.notes == "n"
    with pytest.raises(ValidationError):
        db.update_job(job.id, status="published")  # status only via transition()
    with pytest.raises(ValidationError):
        db.update_job(job.id, id="job_other")
    with pytest.raises(NotFoundError):
        db.update_job("job_missing", title="x")


def test_record_gate_validation_and_storage(db):
    job = db.add_job(brand="b", pillar="p", language="l")
    db.transition(job.id, "rendering")
    db.transition(job.id, "rendered")
    got = db.record_gate(job.id, 0.87, detail={"checks": {"integrity": "pass"}, "n": 2})
    assert got.gate_score == pytest.approx(0.87)
    assert got.gate_json is not None
    import json

    assert json.loads(got.gate_json)["checks"]["integrity"] == "pass"
    for bad in (-0.1, 1.1, "nan-string"):
        with pytest.raises(ValidationError):
            db.record_gate(job.id, bad)


def test_counts_zero_filled(db):
    assert db.counts() == {
        "queued": 0, "rendering": 0, "rendered": 0, "gated": 0,
        "approved": 0, "rejected": 0, "published": 0, "failed": 0,
    }
    job = db.add_job(brand="b", pillar="p", language="l")
    db.transition(job.id, "rendering")
    counts = db.counts()
    assert counts["rendering"] == 1 and counts["queued"] == 0


def test_events_store_json_meta_and_limit(db):
    job = db.add_job(brand="b", pillar="p", language="l")
    db.log_event(job.id, "note", {"a": 1, "b": [1, 2]})
    events = db.get_events(job.id)
    assert events[-1]["meta"] == {"a": 1, "b": [1, 2]}
    assert events[-1]["event"] == "note"
    with pytest.raises(ValidationError):
        db.get_events(job.id, limit=0)
    # events are global-queryable too
    assert db.get_events(limit=10)


def test_connection_is_wal_and_busy_tolerant(tmp_path):
    db = FactoryDB(tmp_path / "factory.db")
    try:
        mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        timeout = db._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == FactoryDB.BUSY_TIMEOUT_MS
    finally:
        db.close()


def test_create_false_requires_existing_file(tmp_path):
    from factory_core.jobqueue import init_db

    with pytest.raises(NotFoundError):
        FactoryDB(tmp_path / "missing.db", create=False)
    handle = init_db(tmp_path / "made.db")
    handle.close()
    reopened = FactoryDB(tmp_path / "made.db", create=False)
    try:
        assert reopened.tables() == ["events", "jobs"]
    finally:
        reopened.close()


def test_raw_sqlite_compatibility(tmp_path):
    """W2/W7 may open the same file directly; the schema must behave."""
    db = FactoryDB(tmp_path / "factory.db")
    job = db.add_job(brand="b", pillar="p", language="l")
    db.close()
    conn = sqlite3.connect(str(tmp_path / "factory.db"))
    try:
        row = conn.execute("SELECT id, status FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert row == (job.id, "queued")
    finally:
        conn.close()


def test_no_writes_outside_db_path(tmp_path):
    """The queue only ever touches its own DB files inside the configured dir."""
    db_dir = tmp_path / "isolation"
    db_dir.mkdir()
    before = set(Path(tmp_path).rglob("*"))
    db = FactoryDB(db_dir / "factory.db")
    job = db.add_job(brand="b", pillar="p", language="l")
    db.transition(job.id, "rendering")
    db.close()
    after = set(Path(tmp_path).rglob("*"))
    created = {p for p in after - before}
    for path in created:
        assert db_dir in path.parents or path == db_dir or path.parent == db_dir
