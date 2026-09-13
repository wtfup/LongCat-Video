"""W2 - WTF Avatar Factory approval console (FastAPI application).

Local-only approval UI for the avatar factory queue:

* reads/writes the shared factory SQLite DB (schema v1, owned by W1)
* serves the SPA from ``./static`` (queue grid + preview drawer + approve/reject)
* serves QA-gated render files from a guarded media root (fail closed)

Run (BRIEF.md contract):

    cd /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/console
    python -m uvicorn app:app --port 8795 --host 127.0.0.1

Dark by construction: no outbound network calls anywhere in this module
(tests prove it by blocking sockets during a full request cycle).
"""

from __future__ import annotations

import mimetypes
import re
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import db as factory_db

VERSION = "w2-console-1.0.0"
SERVICE = "wtf-avatar-console"
PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
LANG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,15}$")


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------


class JobCreate(BaseModel):
    """POST /jobs body. brand/pillar/language are required by the schema."""

    id: Optional[str] = None
    brand: str
    pillar: str
    language: str
    title: Optional[str] = None
    script: Optional[str] = None

    @field_validator("brand", "pillar")
    @classmethod
    def _non_empty(cls, v: str, info) -> str:
        cleaned = (v or "").strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        if len(cleaned) > 120:
            raise ValueError(f"{info.field_name} is too long (max 120 chars)")
        return cleaned

    @field_validator("language")
    @classmethod
    def _language(cls, v: str) -> str:
        cleaned = (v or "").strip().lower()
        if not LANG_RE.match(cleaned):
            raise ValueError(
                "language must be a short lowercase token such as 'hi', 'en' or 'hinglish'"
            )
        return cleaned

    @field_validator("id")
    @classmethod
    def _job_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not ID_RE.match(cleaned):
            raise ValueError("id must be 3-64 chars of [A-Za-z0-9._-]")
        return cleaned

    @field_validator("title")
    @classmethod
    def _title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if len(cleaned) > 300:
            raise ValueError("title is too long (max 300 chars)")
        return cleaned or None

    @field_validator("script")
    @classmethod
    def _script(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if len(v) > 20000:
            raise ValueError("script is too long (max 20000 chars)")
        return v


class RejectBody(BaseModel):
    """Optional POST /jobs/{id}/reject body."""

    reason: Optional[str] = None

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if len(cleaned) > 500:
            raise ValueError("reason is too long (max 500 chars)")
        return cleaned or None


# --------------------------------------------------------------------------
# Application factory
# --------------------------------------------------------------------------


def create_app(
    db_path: Optional[str] = None, media_root: Optional[str] = None
) -> FastAPI:
    """Build the console app. Explicit paths win over env vars over canonical."""
    resolved_db = factory_db.resolve_db_path(db_path)
    resolved_media = factory_db.resolve_media_root(media_root)

    app = FastAPI(
        title="WTF Avatar Factory Console",
        version=VERSION,
        description=(
            "Local approval console for the WTF avatar factory queue. "
            "Dark by construction: makes zero network calls."
        ),
    )

    # ---------------------------------------------------------------- health
    @app.get("/health")
    def health():
        try:
            stats = factory_db.health(resolved_db)
        except Exception as exc:  # unusable DB location, locked file, ...
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "service": SERVICE,
                    "version": VERSION,
                    "db_ok": False,
                    "db_path": resolved_db,
                    "error": f"{type(exc).__name__}: {exc}",
                    "ts": factory_db.utc_now(),
                },
            )
        return {
            "status": "ok",
            "service": SERVICE,
            "version": VERSION,
            "db_ok": True,
            "db_path": resolved_db,
            "jobs_total": stats["jobs_total"],
            "by_status": stats["by_status"],
            "ts": factory_db.utc_now(),
        }

    # ------------------------------------------------------------------ jobs
    @app.get("/jobs")
    def list_jobs(
        status: Optional[str] = Query(None),
        brand: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        if status is not None and status not in factory_db.STATUSES:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unknown_status",
                    "allowed": list(factory_db.STATUSES),
                },
            )
        try:
            jobs = factory_db.list_jobs(
                resolved_db,
                status=status,
                brand=brand,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "db_unavailable", "message": str(exc)},
            )
        return {"count": len(jobs), "jobs": jobs}

    @app.post("/jobs", status_code=201)
    def create_job(payload: JobCreate):
        try:
            job = factory_db.create_job(
                resolved_db,
                job_id=payload.id,
                brand=payload.brand,
                pillar=payload.pillar,
                language=payload.language,
                title=payload.title,
                script=payload.script,
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail={"error": "duplicate_id", "id": payload.id},
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "db_unavailable", "message": str(exc)},
            )
        return job

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            job = factory_db.get_job(resolved_db, job_id)
            if job is None:
                raise HTTPException(
                    status_code=404, detail={"error": "not_found", "id": job_id}
                )
            events = factory_db.get_events(resolved_db, job_id)
            render = factory_db.render_info(job, resolved_media)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "db_unavailable", "message": str(exc)},
            )
        return {**job, "events": events, **render}

    @app.get("/jobs/{job_id}/render")
    def get_render(job_id: str):
        try:
            job = factory_db.get_job(resolved_db, job_id)
            if job is None:
                raise HTTPException(
                    status_code=404, detail={"error": "not_found", "id": job_id}
                )
            path, reason = factory_db.render_file_for(job, resolved_media)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "db_unavailable", "message": str(exc)},
            )
        if path is None:
            code = 404 if reason in ("no_render_path", "file_missing") else 403
            raise HTTPException(
                status_code=code,
                detail={"error": "render_unavailable", "reason": reason},
            )
        media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return FileResponse(str(path), media_type=media_type)

    # ------------------------------------------------------------- decisions
    def _decide(job_id: str, decision: str, reason: Optional[str] = None):
        try:
            result, current = factory_db.decide(
                resolved_db, job_id, decision, reason=reason
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "db_unavailable", "message": str(exc)},
            )
        if result == "not_found":
            raise HTTPException(
                status_code=404, detail={"error": "not_found", "id": job_id}
            )
        if result == "invalid":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "invalid_transition",
                    "current": current,
                    "allowed_from": list(factory_db.DECISION_FROM),
                },
            )
        if result == "idempotent":
            return {
                "ok": True,
                "id": job_id,
                "status": current,
                "changed": False,
                "note": f"already {current}",
            }
        return {"ok": True, "id": job_id, "status": decision, "changed": True}

    @app.post("/jobs/{job_id}/approve")
    def approve_job(job_id: str):
        return _decide(job_id, "approved")

    @app.post("/jobs/{job_id}/reject")
    def reject_job(job_id: str, payload: Optional[RejectBody] = None):
        reason = payload.reason if payload is not None else None
        return _decide(job_id, "rejected", reason=reason)

    # ------------------------------------------------------------------- spa
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def spa_index():
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=500,
                detail={"error": "spa_missing", "path": str(index)},
            )
        return FileResponse(
            str(index), media_type="text/html", headers={"Cache-Control": "no-store"}
        )

    return app


app = create_app()
