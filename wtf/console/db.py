"""SQLite access layer for the WTF Avatar Factory console (work package W2).

Schema v1 is owned by W1 (factory-core). This module uses the exact same DDL
and the exact same canonical DB path so the console, the factory orchestrator
and the QA gate all share one file:

    /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db

Override the path for tests/demos with the ``WTF_FACTORY_DB`` environment
variable or by passing an explicit path to ``create_app()`` / ``connect()``.

Stdlib only (sqlite3). No network access of any kind.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Canonical locations and the shared schema v1 contract (BRIEF.md)
# --------------------------------------------------------------------------

CANONICAL_DB_PATH = (
    "/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db"
)
CANONICAL_MEDIA_ROOT = (
    "/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/renders"
)
ENV_DB_PATH = "WTF_FACTORY_DB"
ENV_MEDIA_ROOT = "WTF_MEDIA_ROOT"

STATUSES = (
    "queued",
    "rendering",
    "rendered",
    "gated",
    "approved",
    "rejected",
    "published",
    "failed",
)

#: Only jobs that have passed the QA gate (W7) can be approved or rejected
#: from the console. See README section 3 for the rationale.
DECISION_FROM = ("gated",)

EVENT_CREATED = "created"
#: Decision transitions use W1 factory-core's event vocabulary (payload:
#: ``{"from": ..., "to": ..., "by": ...}``) so the shared events table reads
#: uniformly across all factory components.
EVENT_STATUS_CHANGE = "status_change"

SCHEMA_SQL = """
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
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  event TEXT NOT NULL,
  meta TEXT
);
"""

#: Exact column lists from the schema v1 contract, used by tests/self-audit.
EXPECTED_JOBS_COLUMNS = (
    "id",
    "created_at",
    "brand",
    "pillar",
    "language",
    "title",
    "script",
    "status",
    "render_path",
    "gate_score",
    "gate_json",
    "notes",
)
EXPECTED_EVENTS_COLUMNS = ("id", "job_id", "ts", "event", "meta")


# --------------------------------------------------------------------------
# Path / time helpers
# --------------------------------------------------------------------------


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string with offset."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_db_path(explicit: Optional[str] = None) -> str:
    """Resolve the factory DB path: explicit arg > env var > canonical path."""
    if explicit:
        return str(explicit)
    return os.environ.get(ENV_DB_PATH, CANONICAL_DB_PATH)


def resolve_media_root(explicit: Optional[str] = None) -> str:
    """Resolve the media root: explicit arg > env var > canonical path."""
    if explicit:
        return str(explicit)
    return os.environ.get(ENV_MEDIA_ROOT, CANONICAL_MEDIA_ROOT)


def new_job_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"job_{stamp}_{uuid.uuid4().hex[:6]}"


# --------------------------------------------------------------------------
# Connection handling
# --------------------------------------------------------------------------


def connect(db_path: str, ensure_schema: bool = True) -> sqlite3.Connection:
    """Open a connection with the shared schema ensured.

    Parent directories are created on demand so a fresh box can bootstrap
    the factory with the very first console request. Raises the underlying
    OSError/sqlite3 error if the location is unusable (the API maps that to
    a 503 - see ``health``).
    """
    path = Path(str(db_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 10000")
    if ensure_schema:
        con.executescript(SCHEMA_SQL)
    return con


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _insert_event(
    con: sqlite3.Connection, job_id: str, event: str, meta: Optional[dict] = None
) -> None:
    con.execute(
        "INSERT INTO events (job_id, ts, event, meta) VALUES (?, ?, ?, ?)",
        (
            job_id,
            utc_now(),
            event,
            json.dumps(meta, separators=(",", ":")) if meta is not None else None,
        ),
    )


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


def create_job(
    db_path: str,
    *,
    job_id: Optional[str] = None,
    brand: str,
    pillar: str,
    language: str,
    title: Optional[str] = None,
    script: Optional[str] = None,
    status: str = "queued",
    render_path: Optional[str] = None,
    gate_score: Optional[float] = None,
    gate_json: Optional[str] = None,
    notes: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a job row plus its ``created`` event.

    The HTTP API always turns this into a ``queued`` job; the extra keyword
    arguments exist so tests, demos and seeding scripts can materialise jobs
    at any lifecycle stage without touching the DB by hand.

    Raises ``sqlite3.IntegrityError`` on a duplicate id.
    """
    jid = job_id or new_job_id()
    now = created_at or utc_now()
    con = connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            """
            INSERT INTO jobs
              (id, created_at, brand, pillar, language, title, script, status,
               render_path, gate_score, gate_json, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                jid,
                now,
                brand,
                pillar,
                language,
                title,
                script,
                status,
                render_path,
                gate_score,
                gate_json,
                notes,
            ),
        )
        _insert_event(con, jid, EVENT_CREATED, {"status": status, "source": "console"})
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        con.close()
        raise
    con.close()
    job = get_job(db_path, jid)
    assert job is not None  # just inserted
    return job


def get_job(db_path: str, job_id: str) -> Optional[Dict[str, Any]]:
    con = connect(db_path)
    try:
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def list_jobs(
    db_path: str,
    *,
    status: Optional[str] = None,
    brand: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    where: List[str] = []
    params: List[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if brand:
        where.append("brand = ?")
        params.append(brand)
    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([int(limit), int(offset)])
    con = connect(db_path)
    try:
        rows = con.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()


def get_events(db_path: str, job_id: str) -> List[Dict[str, Any]]:
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, job_id, ts, event, meta FROM events WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()


def decide(
    db_path: str, job_id: str, decision: str, reason: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """Approve or reject a job atomically.

    Returns ``(result, status)``:

    * ``("not_found", None)``          - no such job
    * ``("idempotent", current)``      - job already in the target state
    * ``("invalid", current)``         - transition not allowed from ``current``
    * ``("decided", decision)``        - transition applied, event recorded

    Only ``gated`` jobs can be decided (BRIEF status flow:
    ``... rendered -> gated -> approved | rejected ...``).
    """
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    con = connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT status, notes FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            con.execute("ROLLBACK")
            return ("not_found", None)
        current = row["status"]
        if current == decision:
            con.execute("ROLLBACK")
            return ("idempotent", current)
        if current not in DECISION_FROM:
            con.execute("ROLLBACK")
            return ("invalid", current)

        meta: Dict[str, Any] = {"from": current, "to": decision, "by": "console"}
        if decision == "rejected" and reason:
            meta["reason"] = reason
            notes = (row["notes"] + "\n" if row["notes"] else "") + f"rejected: {reason}"
            con.execute(
                "UPDATE jobs SET status = ?, notes = ? WHERE id = ?",
                (decision, notes, job_id),
            )
        else:
            con.execute(
                "UPDATE jobs SET status = ? WHERE id = ?", (decision, job_id)
            )
        _insert_event(con, job_id, EVENT_STATUS_CHANGE, meta)
        con.execute("COMMIT")
        return ("decided", decision)
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


def health(db_path: str) -> Dict[str, Any]:
    """Return ``{"jobs_total": int, "by_status": {...}}`` or raise."""
    con = connect(db_path)
    try:
        total = con.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        rows = con.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status ORDER BY status"
        ).fetchall()
        return {
            "jobs_total": int(total),
            "by_status": {r["status"]: int(r["n"]) for r in rows},
        }
    finally:
        con.close()


# --------------------------------------------------------------------------
# Render-file resolution (fail closed)
# --------------------------------------------------------------------------


def render_file_for(
    job: Dict[str, Any], media_root: str
) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve a job's render file, returning ``(path, None)`` or ``(None, reason)``.

    The console only ever serves files that resolve inside ``media_root``;
    ``..`` traversal and symlinks that escape the root are refused, remote
    references are never proxied.
    """
    raw = job.get("render_path")
    if not raw:
        return None, "no_render_path"
    text = str(raw)
    if "://" in text:
        return None, "remote_reference"
    root = Path(media_root).resolve()
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError):
        # unresolvable / embedded-null / absurd-length paths fail closed
        return None, "unresolvable_path"
    if not resolved.is_relative_to(root):
        return None, "outside_media_root"
    try:
        if not resolved.is_file():
            return None, "file_missing"
    except (OSError, ValueError):
        return None, "unresolvable_path"
    return resolved, None


def render_info(job: Dict[str, Any], media_root: str) -> Dict[str, Any]:
    """Small payload helper for GET /jobs/{id}."""
    path, reason = render_file_for(job, media_root)
    if path is None:
        return {"render_available": False, "render_url": None, "render_note": reason}
    return {
        "render_available": True,
        "render_url": f"/jobs/{job['id']}/render",
        "render_note": None,
    }
