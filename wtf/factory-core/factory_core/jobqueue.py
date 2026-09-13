#!/usr/bin/env python3
"""jobqueue.py - SQLite job queue for the WTF Avatar Factory (schema v1).

Owned by W1 (factory-core). The database path is the shared interface contract
from BRIEF.md and must not change:

    /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db

Status machine (schema v1):

    queued -> rendering -> rendered -> gated -> approved | rejected -> published
    (any active status -> failed on error; failed -> queued only via requeue())

Design notes:
- stdlib only (sqlite3 / json / uuid / datetime / pathlib / dataclasses)
- zero network access, zero secrets, no writes outside the configured DB path
- status changes go through transition()/fail_job()/requeue() only, so the
  state machine cannot be bypassed by ad-hoc UPDATEs
- every state change appends a row to the events table (audit trail for W2/W7)
- WAL journaling + busy timeout so the console (W2) can read while the
  pipeline writes
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

__all__ = [
    "DEFAULT_DB_PATH",
    "WTF_DIR",
    "PACKAGE_ROOT",
    "STATUSES",
    "TRANSITIONS",
    "TERMINAL_STATUSES",
    "JOB_FIELDS",
    "MUTABLE_FIELDS",
    "SCHEMA_JOBS_SQL",
    "SCHEMA_EVENTS_SQL",
    "SCHEMA_SQL",
    "FactoryDB",
    "Job",
    "JobQueueError",
    "NotFoundError",
    "InvalidTransition",
    "ConcurrentUpdateError",
    "ValidationError",
    "init_db",
    "utc_now",
]

# --------------------------------------------------------------------------
# Contract constants (schema v1 - do not deviate)
# --------------------------------------------------------------------------

PKG_FILE = Path(__file__).resolve()
PACKAGE_DIR = PKG_FILE.parent                    # .../wtf/factory-core/factory_core
PACKAGE_ROOT = PACKAGE_DIR.parent                # .../wtf/factory-core
WTF_DIR = PACKAGE_ROOT.parent                    # .../wtf

#: Shared interface contract path (BRIEF.md, schema v1). Never change.
DEFAULT_DB_PATH = WTF_DIR / "_data" / "factory.db"

SCHEMA_JOBS_SQL = """\
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
)"""

SCHEMA_EVENTS_SQL = """\
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  event TEXT NOT NULL,
  meta TEXT
)"""

SCHEMA_SQL = SCHEMA_JOBS_SQL + ";\n" + SCHEMA_EVENTS_SQL + ";\n"

#: Lifecycle order (schema v1).
STATUS_QUEUED = "queued"
STATUS_RENDERING = "rendering"
STATUS_RENDERED = "rendered"
STATUS_GATED = "gated"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"

STATUSES = (
    STATUS_QUEUED,
    STATUS_RENDERING,
    STATUS_RENDERED,
    STATUS_GATED,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_PUBLISHED,
    STATUS_FAILED,
)

TERMINAL_STATUSES = (STATUS_PUBLISHED, STATUS_REJECTED)

#: Allowed forward transitions (schema v1). ``failed -> queued`` is handled
#: exclusively by :meth:`FactoryDB.requeue` (explicit operator action).
TRANSITIONS = {
    STATUS_QUEUED: (STATUS_RENDERING, STATUS_FAILED),
    STATUS_RENDERING: (STATUS_RENDERED, STATUS_FAILED),
    STATUS_RENDERED: (STATUS_GATED, STATUS_FAILED),
    STATUS_GATED: (STATUS_APPROVED, STATUS_REJECTED, STATUS_FAILED),
    STATUS_APPROVED: (STATUS_PUBLISHED, STATUS_FAILED),
    STATUS_REJECTED: (),
    STATUS_PUBLISHED: (),
    STATUS_FAILED: (),
}

JOB_FIELDS = (
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

#: Fields a caller may update with :meth:`FactoryDB.update_job`.
#: ``status`` is intentionally excluded - use transition()/fail_job()/requeue().
MUTABLE_FIELDS = ("title", "script", "render_path", "gate_score", "gate_json", "notes")


class JobQueueError(Exception):
    """Base error for all job-queue failures."""


class NotFoundError(JobQueueError):
    """Raised when a job id does not exist."""


class InvalidTransition(JobQueueError):
    """Raised when a status change violates the schema-v1 state machine."""


class ConcurrentUpdateError(JobQueueError):
    """Raised when a job row changed between read and write (optimistic lock)."""


class ValidationError(JobQueueError):
    """Raised on invalid input (missing required field, bad status, bad score)."""


def utc_now() -> str:
    """ISO-8601 UTC timestamp used for ``created_at`` / event ``ts``."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """A single factory job row (schema v1)."""

    id: str
    created_at: str
    brand: str
    pillar: str
    language: str
    title: Optional[str] = None
    script: Optional[str] = None
    status: str = STATUS_QUEUED
    render_path: Optional[str] = None
    gate_score: Optional[float] = None
    gate_json: Optional[str] = None
    notes: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {name: getattr(self, name) for name in JOB_FIELDS}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        return cls(**{name: row[name] for name in JOB_FIELDS})


def _require_text(field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"field {field!r} must be a non-empty string")
    return value.strip()


class FactoryDB:
    """SQLite-backed factory queue (schema v1).

    Parameters
    ----------
    db_path:
        Path to the factory database. Defaults to the shared contract path
        :data:`DEFAULT_DB_PATH`.
    create:
        When True (default) the parent directory and schema are created if
        missing. When False the DB file must already exist.
    clock:
        Injectable UTC timestamp source (tests use a deterministic clock).
    """

    BUSY_TIMEOUT_MS = 5000

    def __init__(
        self,
        db_path: Union[str, Path] = DEFAULT_DB_PATH,
        *,
        create: bool = True,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.path = Path(db_path).expanduser()
        self._clock = clock
        if not create and not self.path.exists():
            raise NotFoundError(f"database does not exist: {self.path}")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=self.BUSY_TIMEOUT_MS / 1000.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=%d" % self.BUSY_TIMEOUT_MS)
        self._conn.execute("PRAGMA foreign_keys=ON")
        if create:
            self.init_schema()

    # -- lifecycle ---------------------------------------------------------

    def init_schema(self) -> None:
        """Create the schema-v1 tables if they do not exist (idempotent)."""
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def tables(self) -> List[str]:
        """Table names in the database (schema verification helper)."""
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]


    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:  # pragma: no cover - defensive
            pass

    def __enter__(self) -> "FactoryDB":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- jobs ---------------------------------------------------------------

    def add_job(
        self,
        *,
        brand: str,
        pillar: str,
        language: str,
        title: Optional[str] = None,
        script: Optional[str] = None,
        job_id: Optional[str] = None,
        status: str = STATUS_QUEUED,
        notes: Optional[str] = None,
    ) -> Job:
        """Insert a new job and log a ``created`` event."""
        brand = _require_text("brand", brand)
        pillar = _require_text("pillar", pillar)
        language = _require_text("language", language)
        if status not in STATUSES:
            raise ValidationError(f"unknown status: {status!r}")
        jid = job_id or ("job_" + uuid.uuid4().hex[:12])
        created = self._clock()
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO jobs (id, created_at, brand, pillar, language, "
                    "title, script, status, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (jid, created, brand, pillar, language, title, script, status, notes),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"job already exists: {jid}") from exc
        self.log_event(jid, "created", {"status": status})
        return self.get_job(jid)

    def get_job(self, job_id: str) -> Job:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no such job: {job_id}")
        return Job.from_row(row)

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        newest_first: bool = False,
    ) -> List[Job]:
        """List jobs, optionally filtered by status (FIFO insertion order by default)."""
        if status is not None and status not in STATUSES:
            raise ValidationError(f"unknown status: {status!r}")
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise ValidationError("limit must be a positive integer")
        sql = "SELECT * FROM jobs"
        params: List[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        # rowid = insertion order (FIFO); created_at can collide within a second
        order = "DESC" if newest_first else "ASC"
        sql += f" ORDER BY rowid {order}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [Job.from_row(row) for row in self._conn.execute(sql, params).fetchall()]

    def counts(self) -> Dict[str, int]:
        """Job counts per status (every status present, zero-filled)."""
        out = {name: 0 for name in STATUSES}
        for row in self._conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
            out[row["status"]] = int(row["n"])
        return out

    def update_job(self, job_id: str, /, **fields: Any) -> Job:
        """Update mutable job columns (never ``status`` - use transition())."""
        if not fields:
            return self.get_job(job_id)
        bad = sorted(set(fields) - set(MUTABLE_FIELDS))
        if bad:
            raise ValidationError(
                f"fields not updatable via update_job: {bad} "
                f"(allowed: {sorted(MUTABLE_FIELDS)}; status changes use transition())"
            )
        if "gate_score" in fields and fields["gate_score"] is not None:
            fields["gate_score"] = self._validate_score(fields["gate_score"])
        assigns = ", ".join(f"{name} = ?" for name in fields)
        params = list(fields.values()) + [job_id]
        with self._conn:
            cur = self._conn.execute(f"UPDATE jobs SET {assigns} WHERE id = ?", params)
        if cur.rowcount != 1:
            raise NotFoundError(f"no such job: {job_id}")
        return self.get_job(job_id)

    def transition(
        self,
        job_id: str,
        to_status: str,
        *,
        reason: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Job:
        """Move a job to ``to_status`` enforcing the schema-v1 state machine."""
        if to_status not in STATUSES:
            raise ValidationError(f"unknown status: {to_status!r}")
        job = self.get_job(job_id)
        allowed = TRANSITIONS[job.status]
        if to_status not in allowed:
            raise InvalidTransition(
                f"illegal transition for {job_id}: {job.status} -> {to_status} "
                f"(allowed: {list(allowed)})"
            )
        with self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET status = ? WHERE id = ? AND status = ?",
                (to_status, job_id, job.status),
            )
        if cur.rowcount != 1:
            raise ConcurrentUpdateError(
                f"concurrent update for {job_id}: expected status {job.status!r}"
            )
        payload: Dict[str, Any] = {"from": job.status, "to": to_status}
        if reason:
            payload["reason"] = reason
        if meta:
            payload.update(meta)
        self.log_event(job_id, "status_change", payload)
        return self.get_job(job_id)

    def fail_job(self, job_id: str, reason: str) -> Job:
        """Mark an active job failed (idempotent when already failed)."""
        job = self.get_job(job_id)
        if job.status == STATUS_FAILED:
            return job
        if job.status in TERMINAL_STATUSES:
            raise InvalidTransition(f"cannot fail terminal job {job_id} ({job.status})")
        return self.transition(job_id, STATUS_FAILED, reason=reason)

    def requeue(self, job_id: str, *, reason: Optional[str] = None) -> Job:
        """Explicit operator requeue: failed -> queued."""
        job = self.get_job(job_id)
        if job.status != STATUS_FAILED:
            raise InvalidTransition(f"requeue requires failed status, got {job.status!r} ({job_id})")
        with self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET status = ? WHERE id = ? AND status = ?",
                (STATUS_QUEUED, job_id, STATUS_FAILED),
            )
        if cur.rowcount != 1:
            raise ConcurrentUpdateError(f"concurrent update for {job_id}: expected status 'failed'")
        payload: Dict[str, Any] = {"from": STATUS_FAILED, "to": STATUS_QUEUED}
        if reason:
            payload["reason"] = reason
        self.log_event(job_id, "status_change", payload)
        return self.get_job(job_id)

    def record_gate(
        self,
        job_id: str,
        score: float,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Job:
        """Write ``gate_score`` / ``gate_json`` (the W7 QA-gate write path)."""
        score = self._validate_score(score)
        gate_json = json.dumps(detail, sort_keys=True) if detail is not None else None
        return self.update_job(job_id, gate_score=score, gate_json=gate_json)

    # -- events -------------------------------------------------------------

    def log_event(self, job_id: str, event: str, meta: Optional[Dict[str, Any]] = None) -> int:
        """Append an audit event; returns the event row id."""
        _require_text("event", event)
        ts = self._clock()
        meta_json = json.dumps(meta, sort_keys=True, default=str) if meta is not None else None
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO events (job_id, ts, event, meta) VALUES (?, ?, ?, ?)",
                (job_id, ts, event, meta_json),
            )
        return int(cur.lastrowid or 0)

    def get_events(self, job_id: Optional[str] = None, *, limit: int = 100) -> List[Dict[str, Any]]:
        """Event audit trail, oldest first (optionally for one job)."""
        if not isinstance(limit, int) or limit < 1:
            raise ValidationError("limit must be a positive integer")
        sql = "SELECT id, job_id, ts, event, meta FROM events"
        params: List[Any] = []
        if job_id is not None:
            sql += " WHERE job_id = ?"
            params.append(job_id)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        out: List[Dict[str, Any]] = []
        for row in self._conn.execute(sql, params).fetchall():
            item = {"id": row["id"], "job_id": row["job_id"], "ts": row["ts"], "event": row["event"]}
            item["meta"] = json.loads(row["meta"]) if row["meta"] else None
            out.append(item)
        return out

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _validate_score(score: Any) -> float:
        try:
            value = float(score)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"gate score must be numeric, got {score!r}") from exc
        if not (0.0 <= value <= 1.0):
            raise ValidationError(f"gate score must be in [0, 1], got {value!r}")
        return value


def init_db(db_path: Union[str, Path] = DEFAULT_DB_PATH) -> FactoryDB:
    """Create (if needed) and return a schema-v1 database handle."""
    return FactoryDB(db_path, create=True)
