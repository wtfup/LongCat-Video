#!/usr/bin/env python3
"""Evidence demo: gate rendered jobs end-to-end through a canonical-schema DB.

Uses an ISOLATED demo DB inside evidence/ — the shared factory.db is never touched.
"""
import json
import sqlite3
import sys
from pathlib import Path

PKG = Path("/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/qa-gate")
EV = PKG / "evidence"
DEMO = EV / "demo_media"
DB = EV / "demo_factory.db"

sys.path.insert(0, str(PKG))
import gate  # noqa: E402

if DB.exists():
    DB.unlink()

con = sqlite3.connect(DB)
gate.ensure_schema(con)
jobs = [
    ("demo-001", "good.mp4", "rendered"),
    ("demo-002", "black.mp4", "rendered"),
    ("demo-003", "silent.mp4", "rendered"),
]
for jid, media, status in jobs:
    con.execute(
        "INSERT INTO jobs (id, created_at, brand, pillar, language, title, status, render_path) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (jid, "2026-09-13T00:00:00Z", "WTF", "AI systems", "en",
         f"demo {media}", status, str(DEMO / media)),
    )
con.commit()
con.close()

print("== gate_job() per job ==")
for jid, media, _ in jobs:
    res = gate.gate_job(str(DB), jid)
    print(json.dumps({
        "job_id": res["job_id"],
        "status": res["status"],
        "score": res["score"],
        "verdict": res["report"]["verdict"],
        "codes": res["report"]["codes"],
    }, indent=2))

print()
print("== resulting jobs rows ==")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
for row in con.execute("SELECT id, status, gate_score, substr(gate_json,1,90) AS gate_json_head FROM jobs ORDER BY id"):
    print(json.dumps(dict(row)))

print()
print("== resulting events rows ==")
for row in con.execute("SELECT job_id, event, meta FROM events ORDER BY id"):
    print(json.dumps(dict(row)))

print()
print("== fail-closed checks ==")
for attempt in (
    ("ghost", {"verdict": "accept", "score": 90.0}, "unknown_job"),
    ("demo-001", {"verdict": "accept", "score": None}, "accept requires numeric score"),
):
    jid, rep, label = attempt
    try:
        gate.record_result(str(DB), jid, rep)
        print(f"{label}: NOT BLOCKED (BUG!)")
    except gate.DbError as exc:
        print(f"{label}: blocked -> {exc}")
con.close()
