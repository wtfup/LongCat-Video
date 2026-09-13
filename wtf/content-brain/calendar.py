"""W4 Content Brain - content calendar planner + factory.db enqueue.

Turns the pillar library + weekday rotation into a deterministic publishing
plan, and (opt-in) enqueues the matching render jobs into the shared factory
SQLite database using the exact schema from the swarm BRIEF (v1 contract).

Dark by default: planning writes nothing; database writes happen only with an
explicit ``--enqueue`` (plus ``--db``). No network, no external calls.

Quick use:
    python calendar.py --days 7 --start 2026-09-14
    python calendar.py --days 7 --start 2026-09-14 --out calendar.json
    python calendar.py --days 7 --enqueue --db /tmp/factory.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import brain

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = Path("/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db")

FORMAT = brain.VIDEO_FORMAT
DURATION_S = brain.DURATION_S

JOBS_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  brand TEXT NOT NULL,
  pillar TEXT NOT NULL,
  language TEXT NOT NULL,
  title TEXT,
  script TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  render_path TEXT,
  gate_score REAL,
  gate_json TEXT,
  notes TEXT
);
"""

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  event TEXT NOT NULL,
  meta TEXT
);
"""

EXPECTED_JOBS_COLUMNS = {
    "id", "created_at", "brand", "pillar", "language", "title", "script",
    "status", "render_path", "gate_score", "gate_json", "notes",
}


@dataclass(frozen=True)
class PlanSlot:
    slot_index: int
    date: date
    weekday: str
    pillar: str
    topic_id: str
    brief_id: str
    brand: str
    format: str
    duration_s: int
    languages: List[str]


def _resolve_topic(pillar_obj: Dict[str, Any], appearance: int) -> Dict[str, Any]:
    topics = pillar_obj["topics"]
    return topics[appearance % len(topics)]


def plan_days(
    days: int,
    *,
    start: Optional[date] = None,
    per_day: int = 1,
    library: Optional[Dict[str, Any]] = None,
) -> List[PlanSlot]:
    """Deterministic day-by-day plan (primary slot follows the weekly rotation)."""
    if days < 1:
        raise ValueError("days must be >= 1")
    if per_day not in (1, 2):
        raise ValueError("per_day must be 1 or 2 in v1")
    lib = library if library is not None else brain.load_library()
    start = start or brain.today_ist()

    weekly = lib["rotation"]["weekly"]
    secondary = lib["rotation"]["secondary"]
    pillars_by_id = {p["id"]: p for p in lib["pillars"]}
    appearance: Dict[str, int] = {}

    out: List[PlanSlot] = []
    for offset in range(days):
        day = date.fromordinal(start.toordinal() + offset)
        weekday = day.strftime("%A").lower()
        day_pillars = [weekly[weekday]]
        if per_day == 2:
            idx = day.toordinal() % len(secondary)
            alt = secondary[idx]
            if alt == day_pillars[0]:
                alt = secondary[(idx + 1) % len(secondary)]
            day_pillars.append(alt)
        for slot_index, pillar_id in enumerate(day_pillars):
            pillar_obj = pillars_by_id[pillar_id]
            count = appearance.get(pillar_id, 0)
            topic_obj = _resolve_topic(pillar_obj, count)
            appearance[pillar_id] = count + 1
            brand_id = topic_obj.get("brand") or pillar_obj["default_brand"]
            brief_id = "cb-{}-{}-{}".format(day.isoformat(), pillar_id, topic_obj["id"].split("-")[-1])
            out.append(
                PlanSlot(
                    slot_index=slot_index,
                    date=day,
                    weekday=weekday,
                    pillar=pillar_id,
                    topic_id=topic_obj["id"],
                    brief_id=brief_id,
                    brand=brand_id,
                    format=FORMAT,
                    duration_s=DURATION_S,
                    languages=list(brain.LANGUAGES),
                )
            )
    return out


def plan_to_dict(plan: List[PlanSlot], *, days: Optional[int] = None) -> Dict[str, Any]:
    slots = []
    for slot in plan:
        row = asdict(slot)
        row["date"] = slot.date.isoformat()
        slots.append(row)
    return {
        "generated_by": "w4-content-brain",
        "start": plan[0].date.isoformat() if plan else None,
        "days": days if days is not None else (len({s.date for s in plan}) or 0),
        "per_day": len({s.slot_index for s in plan}) or 1,
        "slots": slots,
    }


# ---------------------------------------------------------------------------
# factory.db integration (shared schema v1 — owned by W1)
# ---------------------------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(JOBS_DDL)
    conn.execute(EVENTS_DDL)
    conn.commit()


def _verify_jobs_columns(conn: sqlite3.Connection) -> None:
    names = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    missing = EXPECTED_JOBS_COLUMNS - names
    if missing:
        raise RuntimeError(
            "jobs table does not match the shared schema v1 (missing: {})".format(
                ", ".join(sorted(missing))
            )
        )


def enqueue_plan(
    db_path: str,
    plan: List[PlanSlot],
    *,
    created_at: Optional[str] = None,
    library: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert one queued render job per language per slot. Idempotent by job id."""
    db_file = Path(db_path)
    if db_file.parent:
        db_file.parent.mkdir(parents=True, exist_ok=True)
    lib = library if library is not None else brain.load_library()
    now = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn = sqlite3.connect(str(db_file))
    job_ids: List[str] = []
    try:
        ensure_schema(conn)
        _verify_jobs_columns(conn)
        for slot in plan:
            brief = brain.daily_brief(
                slot.date, pillar=slot.pillar, topic=slot.topic_id,
                variation=slot.slot_index, library=lib,
            )
            for lang in brief.languages:
                job_id = "{}-{}".format(slot.brief_id, lang)
                notes = json.dumps(
                    {
                        "brief_id": slot.brief_id,
                        "topic_id": slot.topic_id,
                        "pillar_label": brief.pillar_label,
                        "brand_label": brief.brand_label,
                        "format": brief.format,
                        "duration_s": brief.duration_s,
                        "languages": list(brief.languages),
                        "word_counts": brief.word_counts,
                        "source": "content-brain-calendar",
                    },
                    ensure_ascii=False,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO jobs "
                    "(id, created_at, brand, pillar, language, title, script, status, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        now,
                        brief.brand,
                        brief.pillar,
                        lang,
                        brief.title[lang],
                        brief.scripts[lang],
                        "queued",
                        notes,
                    ),
                )
                job_ids.append(job_id)
        conn.commit()
    finally:
        conn.close()
    return {"db": str(db_file), "slots": len(plan), "jobs": len(job_ids), "job_ids": job_ids}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="calendar.py", description="W4 Content Brain — calendar")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--start", help="YYYY-MM-DD (default: today, IST)")
    parser.add_argument("--per-day", type=int, default=1, choices=(1, 2))
    parser.add_argument("--json", action="store_true", help="print plan as JSON")
    parser.add_argument("--out", help="write plan JSON to this path")
    parser.add_argument("--enqueue", action="store_true", help="write queued jobs into factory.db")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="factory.db path (with --enqueue)")
    parser.add_argument("--as-of", help="override created_at (ISO-8601) for enqueue")
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start) if args.start else brain.today_ist()
    plan = plan_days(args.days, start=start, per_day=args.per_day)
    payload = plan_to_dict(plan, days=args.days)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote {} ({} slots)".format(out, len(plan)))
    else:
        for slot in plan:
            print("{} {} slot{} {} {} {}".format(
                slot.date.isoformat(), slot.weekday, slot.slot_index,
                slot.pillar, slot.topic_id, slot.brand))

    if args.enqueue:
        result = enqueue_plan(args.db, plan, created_at=args.as_of)
        print("enqueued {} jobs for {} slots into {}".format(
            result["jobs"], result["slots"], result["db"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
