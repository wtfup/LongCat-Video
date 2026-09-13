"""Pipeline tests: happy path, failures, publish gating, requeue, idempotency."""
import json

import pytest

from factory_core.adapters import StageResult, default_adapters
from factory_core.jobqueue import (
    InvalidTransition,
    ValidationError,
)
from factory_core.pipeline import Pipeline


class BadRenderAdapter:
    """Returns ok=False (simulated stage failure)."""

    dark = True

    def run(self, job):
        return StageResult(stage="render", ok=False, mode="dark", detail="simulated render failure")


class ExplodingAdapter:
    """Raises (adapter bug/crash)."""

    dark = True

    def run(self, job):
        raise RuntimeError("boom")


class NoScoreGate:
    dark = True

    def run(self, job):
        return StageResult(stage="gate", ok=True, mode="dark", output={}, detail="no score")


class FailingPublish:
    dark = True

    def run(self, job):
        return StageResult(stage="publish", ok=False, mode="dark", detail="simulated publish failure")


def _mk(db, title="t"):
    return db.add_job(brand="WTF Gyms", pillar="fitness-transformation", language="hindi", title=title)


def test_run_once_returns_none_on_empty_queue(db):
    assert Pipeline(db).run_once() is None


def test_happy_path_reaches_gated_with_events(pipeline):
    job = _mk(pipeline.db)
    out = pipeline.run_once()
    assert out is not None and out.status == "gated"
    assert out.render_path and out.render_path.endswith(f"{job.id}.mp4")
    assert out.gate_score is not None and 0.0 < out.gate_score <= 1.0
    assert json.loads(out.gate_json)["simulated"] is True

    events = [e["event"] for e in pipeline.db.get_events(job.id)]
    assert events[0] == "created"
    assert events.count("stage_start") == 3          # voice, render, gate
    assert events.count("stage_done") == 3
    statuses = [e["meta"]["to"] for e in pipeline.db.get_events(job.id) if e["event"] == "status_change"]
    assert statuses == ["rendering", "rendered", "gated"]
    assert pipeline.captured_log  # pipeline logged stage lines


def test_until_rendered_stops_before_gate(pipeline):
    job = _mk(pipeline.db)
    out = pipeline.run_job(job.id, until="rendered")
    assert out.status == "rendered"
    assert out.gate_score is None
    assert out.render_path


def test_drain_processes_all_queued(pipeline):
    ids = [_mk(pipeline.db, f"job {n}").id for n in range(3)]
    done = pipeline.drain()
    assert [j.status for j in done] == ["gated"] * 3
    assert {j.id for j in done} == set(ids)
    assert pipeline.run_once() is None  # nothing left


def test_adapter_failure_marks_failed_with_reason(db):
    pipeline = Pipeline(db, adapters={**default_adapters(), "render": BadRenderAdapter()})
    job = _mk(db)
    out = pipeline.run_once()
    assert out.status == "failed"
    events = db.get_events(job.id)
    error = [e for e in events if e["event"] == "stage_error"]
    assert len(error) == 1 and error[0]["meta"]["stage"] == "render"
    change = [e for e in events if e["event"] == "status_change"][-1]
    assert change["meta"]["to"] == "failed" and "simulated render failure" in change["meta"]["reason"]


def test_adapter_exception_is_contained(db):
    pipeline = Pipeline(db, adapters={**default_adapters(), "voice": ExplodingAdapter()})
    job = _mk(db)
    out = pipeline.run_once()
    assert out.status == "failed"
    error = [e for e in db.get_events(job.id) if e["event"] == "stage_error"][0]
    assert error["meta"]["stage"] == "voice"
    assert "RuntimeError: boom" in error["meta"]["error"]
    # loop survived and the queue still works
    assert pipeline.run_once() is None


def test_gate_without_score_fails_closed(db):
    pipeline = Pipeline(db, adapters={**default_adapters(), "gate": NoScoreGate()})
    job = _mk(db)
    out = pipeline.run_once()
    assert out.status == "failed"
    reason = [e for e in db.get_events(job.id) if e["event"] == "status_change"][-1]["meta"]["reason"]
    assert "gate" in reason and "score" in reason


def test_run_job_requires_queued_and_requeue_unlocks_retry(db):
    pipeline = Pipeline(db)
    job = _mk(db)
    pipeline.run_once()
    with pytest.raises(InvalidTransition):
        pipeline.run_job(job.id)  # already gated
    failed = db.add_job(brand="b", pillar="p", language="l")
    pipeline.db.transition(failed.id, "rendering")
    db.fail_job(failed.id, "x")
    with pytest.raises(InvalidTransition):
        pipeline.run_job(failed.id)  # still failed; must requeue first
    pipeline.requeue(failed.id, reason="operator retry")
    assert pipeline.run_job(failed.id).status == "gated"


def test_until_validation(db):
    pipeline = Pipeline(db)
    job = _mk(db)
    with pytest.raises(ValidationError):
        pipeline.run_job(job.id, until="published")


def test_missing_or_broken_adapters_rejected(db):
    with pytest.raises(ValidationError):
        Pipeline(db, adapters={"voice": default_adapters()["voice"]})
    with pytest.raises(ValidationError):
        Pipeline(db, adapters={**default_adapters(), "gate": object()})


def test_publish_only_touches_approved(db):
    pipeline = Pipeline(db)
    job = _mk(db)
    pipeline.run_once()  # gated
    assert pipeline.publish_approved() == []          # nothing approved yet
    assert db.get_job(job.id).status == "gated"
    pipeline.approve(job.id, notes="ship it")
    published = pipeline.publish_approved()
    assert [j.status for j in published] == ["published"]
    assert db.get_job(job.id).notes == "ship it"
    assert pipeline.publish_approved() == []          # idempotent, nothing left


def test_reject_flow_never_publishes(db):
    pipeline = Pipeline(db)
    job = _mk(db)
    pipeline.run_once()
    pipeline.reject(job.id, notes="off-brand")
    assert db.get_job(job.id).status == "rejected"
    assert pipeline.publish_approved() == []


def test_publish_failure_marks_failed(db):
    pipeline = Pipeline(db)
    job = _mk(db)
    pipeline.run_once()
    pipeline.approve(job.id)
    failing = Pipeline(db, adapters={**default_adapters(), "publish": FailingPublish()})
    out = failing.publish_approved()
    assert [j.status for j in out] == ["failed"]
    reason = [e for e in db.get_events(job.id) if e["event"] == "status_change"][-1]["meta"]["reason"]
    assert "simulated publish failure" in reason


def test_stats_and_counts_after_lifecycle(db):
    pipeline = Pipeline(db)
    j1, j2 = _mk(db, "a"), _mk(db, "b")
    pipeline.run_once()
    pipeline.run_once()
    pipeline.approve(j1.id)
    pipeline.publish_approved()
    pipeline.reject(j2.id)
    counts = pipeline.stats()
    assert counts["published"] == 1 and counts["rejected"] == 1
    assert counts["queued"] == 0


def test_drain_limit_bounds_work(db):
    for n in range(5):
        _mk(db, f"job {n}")
    pipeline = Pipeline(db)
    done = pipeline.drain(limit=2)
    assert len(done) == 2
    assert db.counts()["queued"] == 3
    with pytest.raises(ValidationError):
        pipeline.drain(limit=0)
