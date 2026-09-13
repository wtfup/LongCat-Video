#!/usr/bin/env python3
"""pipeline.py - orchestrator that advances jobs along the schema-v1 lifecycle.

    queued -> rendering -> rendered -> gated -> approved | rejected -> published

The pipeline is dark by default: it drives the stub adapters from
``adapters.py`` (zero network). Real stage implementations are injected via
``Pipeline(db, adapters={"render": RealRenderAdapter(), ...})``.

Rules enforced here:
- stage failures (``ok=False`` or raised exceptions) mark the job ``failed``
  with the reason captured in the events table - the loop never crashes on a
  bad adapter.
- a job is only ever advanced from its current legal status; run_job requires
  ``queued`` (requeue a failed job explicitly before retrying).
- publishing only ever touches ``approved`` jobs.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from .adapters import REQUIRED_STAGES, StageResult, default_adapters
from .jobqueue import (
    STATUS_APPROVED,
    STATUS_FAILED,
    STATUS_GATED,
    STATUS_PUBLISHED,
    STATUS_QUEUED,
    STATUS_RENDERED,
    STATUS_RENDERING,
    FactoryDB,
    InvalidTransition,
    Job,
    ValidationError,
)

__all__ = ["Pipeline", "UNTIL_CHOICES"]

UNTIL_CHOICES = ("rendered", "gated")


class Pipeline:
    """Stage orchestrator for the factory queue."""

    def __init__(
        self,
        db: FactoryDB,
        *,
        adapters: Optional[Dict[str, Any]] = None,
        log: Optional[Callable[[str], None]] = None,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db = db
        self.adapters: Dict[str, Any] = dict(default_adapters() if adapters is None else adapters)
        missing = [name for name in REQUIRED_STAGES if name not in self.adapters]
        if missing:
            raise ValidationError(f"missing stage adapters: {missing} (required: {list(REQUIRED_STAGES)})")
        for name in REQUIRED_STAGES:
            adapter = self.adapters[name]
            if not hasattr(adapter, "run") or not callable(adapter.run):
                raise ValidationError(f"adapter for stage {name!r} has no callable run(job)")
        self._timer = timer
        self._log = log if log is not None else (lambda _msg: None)

    # -- orchestration -------------------------------------------------------

    def run_once(self, *, until: str = "gated") -> Optional[Job]:
        """Process the oldest queued job; returns None when the queue is empty."""
        queued = self.db.list_jobs(status=STATUS_QUEUED, limit=1)
        if not queued:
            return None
        return self.run_job(queued[0].id, until=until)

    def drain(self, *, limit: int = 100, until: str = "gated") -> List[Job]:
        """Process up to ``limit`` queued jobs; returns the resulting jobs."""
        if not isinstance(limit, int) or limit < 1:
            raise ValidationError("limit must be a positive integer")
        done: List[Job] = []
        while len(done) < limit:
            job = self.run_once(until=until)
            if job is None:
                break
            done.append(job)
        return done

    def run_job(self, job_id: str, *, until: str = "gated") -> Job:
        """Advance one queued job through render (and optionally gate)."""
        if until not in UNTIL_CHOICES:
            raise ValidationError(f"until must be one of {list(UNTIL_CHOICES)}, got {until!r}")
        job = self.db.get_job(job_id)
        if job.status != STATUS_QUEUED:
            raise InvalidTransition(f"run_job requires status {STATUS_QUEUED!r}, got {job.status!r} ({job_id})")
        self.db.transition(job_id, STATUS_RENDERING, reason="pipeline start")

        for stage in ("voice", "render"):
            result = self._call(stage, job)
            if not result.ok:
                return self._fail(job, stage, result)
            if stage == "render":
                render_path = result.output.get("render_path")
                if render_path:
                    self.db.update_job(job_id, render_path=str(render_path))
        self.db.transition(job_id, STATUS_RENDERED, reason="render stage done")
        if until == "rendered":
            return self.db.get_job(job_id)

        gate = self._call("gate", job)
        if not gate.ok:
            return self._fail(job, "gate", gate)
        score = gate.output.get("score")
        if score is None:
            failed = StageResult(stage="gate", ok=False, detail="gate adapter produced no score")
            return self._fail(job, "gate", failed)
        self.db.record_gate(job_id, score, detail=dict(gate.output))
        self.db.transition(job_id, STATUS_GATED, reason="gate stage done")
        return self.db.get_job(job_id)

    def publish_approved(self, *, limit: int = 100) -> List[Job]:
        """Publish up to ``limit`` approved jobs via the publish adapter."""
        if not isinstance(limit, int) or limit < 1:
            raise ValidationError("limit must be a positive integer")
        out: List[Job] = []
        for job in self.db.list_jobs(status=STATUS_APPROVED, limit=limit):
            result = self._call("publish", job)
            if result.ok:
                self.db.transition(job.id, STATUS_PUBLISHED, reason="publish stage done")
            else:
                self._fail(job, "publish", result)
            out.append(self.db.get_job(job.id))
        return out

    # -- operator actions (shared by CLI and console W2) ----------------------

    def approve(self, job_id: str, *, notes: Optional[str] = None) -> Job:
        if notes is not None:
            self.db.update_job(job_id, notes=notes)
        return self.db.transition(job_id, STATUS_APPROVED, reason="operator approved")

    def reject(self, job_id: str, *, notes: Optional[str] = None) -> Job:
        if notes is not None:
            self.db.update_job(job_id, notes=notes)
        return self.db.transition(job_id, "rejected", reason="operator rejected")

    def requeue(self, job_id: str, *, reason: Optional[str] = None) -> Job:
        return self.db.requeue(job_id, reason=reason)

    def stats(self) -> Dict[str, int]:
        return self.db.counts()

    # -- internals ------------------------------------------------------------

    def _call(self, stage: str, job: Job) -> StageResult:
        adapter = self.adapters[stage]
        started = self._timer()
        self.db.log_event(job.id, "stage_start", {"stage": stage})
        try:
            result = adapter.run(job)
        except Exception as exc:  # noqa: BLE001 - adapters must never crash the loop
            result = StageResult(
                stage=stage,
                ok=False,
                mode=getattr(adapter, "mode", "dark"),
                detail=f"{type(exc).__name__}: {exc}",
            )
        elapsed = round(self._timer() - started, 4)
        if not isinstance(result, StageResult):
            result = StageResult(stage=stage, ok=False, detail=f"adapter returned {type(result).__name__}")
        if result.duration_s == 0.0:
            result.duration_s = elapsed
        if result.ok:
            self.db.log_event(
                job.id,
                "stage_done",
                {
                    "stage": stage,
                    "mode": result.mode,
                    "duration_s": result.duration_s,
                    "detail": result.detail,
                },
            )
        else:
            self.db.log_event(job.id, "stage_error", {"stage": stage, "error": result.detail})
        self._log(f"  stage={stage:<7} ok={str(result.ok):<5} mode={result.mode:<4} {result.detail}")
        return result

    def _fail(self, job: Job, stage: str, result: StageResult) -> Job:
        fresh = self.db.get_job(job.id)
        if fresh.status == STATUS_FAILED:
            return fresh
        reason = f"{stage}: {result.detail or 'adapter failed'}"
        self.db.fail_job(job.id, reason)
        self._log(f"  -> {job.id} FAILED ({reason})")
        return self.db.get_job(job.id)
