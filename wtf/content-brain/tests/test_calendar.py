"""Calendar planning + factory.db enqueue contract tests."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import calendar as cal_mod

PKG = Path(__file__).resolve().parents[1]
CAL = PKG / "calendar.py"

EXPECTED_JOBS_COLUMNS = [
    ("id", "TEXT"), ("created_at", "TEXT"), ("brand", "TEXT"), ("pillar", "TEXT"),
    ("language", "TEXT"), ("title", "TEXT"), ("script", "TEXT"), ("status", "TEXT"),
    ("render_path", "TEXT"), ("gate_score", "REAL"), ("gate_json", "TEXT"), ("notes", "TEXT"),
]
EXPECTED_EVENTS_COLUMNS = [
    ("id", "INTEGER"), ("job_id", "TEXT"), ("ts", "TEXT"), ("event", "TEXT"), ("meta", "TEXT"),
]


def test_schema_matches_shared_contract(tmp_path):
    db = tmp_path / "factory.db"
    conn = sqlite3.connect(str(db))
    cal_mod.ensure_schema(conn)
    jobs = [(r[1], r[2].split("(")[0].strip().upper(), r[3]) for r in conn.execute("PRAGMA table_info(jobs)")]
    events = [(r[1], r[2].split("(")[0].strip().upper(), r[3])
              for r in conn.execute("PRAGMA table_info(events)")]
    assert [(n, t) for n, t, _ in jobs] == EXPECTED_JOBS_COLUMNS
    assert [(n, t) for n, t, _ in events] == EXPECTED_EVENTS_COLUMNS
    # status default + primary key as documented
    job_cols = {n: (t, notnull, dflt, pk) for n, t, notnull, dflt, pk in
                [(r[1], r[2], r[3], r[4], r[5]) for r in conn.execute("PRAGMA table_info(jobs)")]}
    assert job_cols["id"][3] == 1  # pk
    assert job_cols["status"][2] == "'queued'"
    conn.close()


def test_plan_7_days_basic_shape():
    plan = cal_mod.plan_days(7, start=date(2026, 9, 14))
    assert len(plan) == 7
    assert [s.date for s in plan] == [date(2026, 9, 14) + timedelta(days=i) for i in range(7)]
    assert {s.pillar for s in plan} == {
        "ai_systems", "founder_journey", "wtf_brands", "fitness_transformation", "business",
    }
    for slot in plan:
        assert slot.languages == ["en", "hi"]
        assert slot.format == "9:16_vertical_reel"
        assert slot.duration_s == 45
        assert slot.brief_id.startswith("cb-")
        assert slot.weekday == slot.date.strftime("%A").lower()


def test_plan_weekday_matches_rotation():
    lib = cal_mod.brain.load_library()
    weekly = lib["rotation"]["weekly"]
    plan = cal_mod.plan_days(14, start=date(2026, 9, 14))
    for slot in plan:
        if slot.slot_index == 0:  # primary slot follows the weekly rotation
            assert weekly[slot.weekday] == slot.pillar


def test_no_topic_repeats_within_a_week_per_pillar():
    plan = cal_mod.plan_days(21, start=date(2026, 9, 14))
    by_pillar = {}
    for slot in plan:
        by_pillar.setdefault(slot.pillar, []).append(slot)
    for pillar, slots in by_pillar.items():
        for i in range(len(slots)):
            window = slots[i:i + 2]  # ≤2 appearances per pillar per 7 days
            window = [s for s in window if (s.date - slots[i].date).days < 7]
            topics = [s.topic_id for s in window]
            assert len(topics) == len(set(topics)), (pillar, topics)


def test_per_day_two_slots_are_distinct_and_cover_secondary():
    plan = cal_mod.plan_days(10, start=date(2026, 9, 14), per_day=2)
    assert len(plan) == 20
    per_date = {}
    for slot in plan:
        per_date.setdefault(slot.date, []).append(slot.pillar)
    for d, pillars in per_date.items():
        assert len(pillars) == 2 and pillars[0] != pillars[1], d
    secondary = {s.pillar for s in plan if s.slot_index == 1}
    assert secondary == {"ai_systems", "founder_journey", "wtf_brands",
                         "fitness_transformation", "business"}


def test_plan_is_deterministic():
    a = cal_mod.plan_days(7, start=date(2026, 9, 14))
    b = cal_mod.plan_days(7, start=date(2026, 9, 14))
    assert a == b


def test_enqueue_writes_exact_job_rows(tmp_path):
    db = tmp_path / "factory.db"
    plan = cal_mod.plan_days(7, start=date(2026, 9, 14))
    fixed_now = "2026-09-13T12:00:00+00:00"
    result = cal_mod.enqueue_plan(str(db), plan, created_at=fixed_now)
    assert result["slots"] == 7
    assert result["jobs"] == 14  # hi + en per brief
    assert len(result["job_ids"]) == 14 == len(set(result["job_ids"]))

    conn = sqlite3.connect(str(db))
    rows = list(conn.execute(
        "SELECT id, created_at, brand, pillar, language, title, script, status, notes FROM jobs ORDER BY id"
    ))
    assert len(rows) == 14
    langs = {r[4] for r in rows}
    assert langs == {"hi", "en"}
    for rid, created_at, brand, pillar, language, title, script, status, notes in rows:
        assert created_at == fixed_now
        assert status == "queued"
        assert brand and pillar and title
        assert len(script) > 200
        assert rid.endswith("-" + language)
        payload = json.loads(notes)
        assert payload["brief_id"] == rid.rsplit("-", 1)[0]
        assert payload["source"] == "content-brain-calendar"
    conn.close()


def test_enqueue_is_idempotent(tmp_path):
    db = tmp_path / "factory.db"
    plan = cal_mod.plan_days(3, start=date(2026, 9, 14))
    cal_mod.enqueue_plan(str(db), plan, created_at="2026-09-13T12:00:00+00:00")
    cal_mod.enqueue_plan(str(db), plan, created_at="2026-09-13T12:05:00+00:00")
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert count == 6  # 3 slots × 2 languages, replaced not duplicated


def test_enqueue_refuses_unknown_extra_columns_safely(tmp_path):
    """Enqueue must not silently corrupt a DB whose jobs table differs."""
    db = tmp_path / "foreign.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, something TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError):
        cal_mod.enqueue_plan(str(db), cal_mod.plan_days(1, start=date(2026, 9, 14)),
                             created_at="2026-09-13T12:00:00+00:00")


def test_cli_json_plan_stdout_pure(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(CAL), "--days", "3", "--start", "2026-09-14", "--json"],
        capture_output=True, text=True, cwd=str(PKG),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["days"] == 3
    assert len(data["slots"]) == 3


def test_cli_plan_writes_file(tmp_path):
    out = tmp_path / "calendar.json"
    proc = subprocess.run(
        [sys.executable, str(CAL), "--days", "2", "--start", "2026-09-14", "--out", str(out)],
        capture_output=True, text=True, cwd=str(PKG),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["slots"][0]["date"] == "2026-09-14"


def test_plan_to_dict_shape():
    plan = cal_mod.plan_days(1, start=date(2026, 9, 14))
    payload = cal_mod.plan_to_dict(plan)
    slot = payload["slots"][0]
    assert set(slot) >= {"date", "weekday", "pillar", "topic_id", "brief_id",
                         "brand", "slot_index", "languages", "format", "duration_s"}
    assert slot["languages"] == ["en", "hi"]
