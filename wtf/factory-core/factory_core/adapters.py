#!/usr/bin/env python3
"""adapters.py - stage adapter stubs for the WTF Avatar Factory (W1 factory-core).

EVERYTHING IN THIS MODULE IS DARK / OFFLINE.

- No network access of any kind (no socket, no urllib, no http, no requests).
- ``dark=True`` is the only constructible mode: constructing an adapter with
  ``dark=False`` raises :class:`LiveAccessRefused` immediately.
- Real stages (LongCat-Avatar render, TTS voice clone, W7 QA gate, W5
  publishers) plug in later by implementing the same ``run(job) ->
  StageResult`` interface and being injected into the pipeline. See BRIEF.md.

Stage contract:
    run(job: Job) -> StageResult

The pipeline owns status transitions; adapters never touch the database.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Type

from .jobqueue import WTF_DIR, Job

__all__ = [
    "DARK",
    "LIVE_ENABLED",
    "NETWORK_ENABLED",
    "REQUIRED_STAGES",
    "ADAPTER_SPEC",
    "LiveAccessRefused",
    "StageResult",
    "DarkAdapter",
    "VoiceAdapter",
    "RenderAdapter",
    "QaGateAdapter",
    "PublisherAdapter",
    "default_adapters",
]

#: Module-level dark-mode flags (schema v1 philosophy: dark by default).
DARK = True
LIVE_ENABLED = False
NETWORK_ENABLED = False

#: Stage names the pipeline requires.
REQUIRED_STAGES = ("voice", "render", "gate", "publish")

#: Placeholder output directory for simulated renders (never created on disk by
#: the dark stubs; the real render-kit owns this path).
RENDERS_DIR = WTF_DIR / "_data" / "renders"


class LiveAccessRefused(RuntimeError):
    """Raised when live (network) behaviour is requested from a dark adapter."""


@dataclass
class StageResult:
    """Uniform result envelope returned by every stage adapter."""

    stage: str
    ok: bool
    mode: str = "dark"
    output: Dict[str, Any] = field(default_factory=dict)
    detail: str = ""
    duration_s: float = 0.0

    def __post_init__(self) -> None:
        if self.mode not in ("dark", "live"):
            raise ValueError(f"invalid mode: {self.mode!r}")
        if not isinstance(self.output, dict):
            raise ValueError("output must be a dict")


class DarkAdapter:
    """Base class for offline stage stubs.

    Construction policy (fail closed): only ``dark=True`` is accepted. Live
    adapters do not exist in factory-core v1, so any attempt to build one
    raises :class:`LiveAccessRefused` instead of silently degrading.
    """

    stage = "dark"

    def __init__(self, *, dark: bool = True) -> None:
        if dark is not True:
            raise LiveAccessRefused(
                f"{type(self).__name__}: live mode is not available in factory-core v1. "
                f"Wire the real '{self.stage}' adapter (render-kit / qa-gate / publishers) "
                "and inject it into the pipeline instead."
            )
        self.dark = True
        self.network_enabled = False

    def run(self, job: Job) -> StageResult:
        """Execute the stage for ``job`` (override ``_run`` in subclasses)."""
        if not isinstance(job, Job):
            raise TypeError(f"{type(self).__name__}.run expects a Job, got {type(job).__name__}")
        return self._run(job)

    def _run(self, job: Job) -> StageResult:  # pragma: no cover - abstract
        raise NotImplementedError(f"{type(self).__name__} must implement _run()")


class VoiceAdapter(DarkAdapter):
    """TTS / voice-clone stage stub. No audio is synthesized, no files written."""

    stage = "voice"

    def _run(self, job: Job) -> StageResult:
        return StageResult(
            stage=self.stage,
            ok=True,
            mode="dark",
            output={
                "audio_path": None,
                "target_seconds": 45,
                "voice": "dark-stub",
                "language": job.language,
                "simulated": True,
            },
            detail="dark voice stub (no TTS, no file)",
        )


class RenderAdapter(DarkAdapter):
    """Avatar render stage stub. Returns a placeholder path; writes nothing."""

    stage = "render"

    def _run(self, job: Job) -> StageResult:
        placeholder = str(RENDERS_DIR / f"{job.id}.mp4")
        return StageResult(
            stage=self.stage,
            ok=True,
            mode="dark",
            output={
                "render_path": placeholder,
                "exists": Path(placeholder).exists(),
                "simulated": True,
                "note": "dark stub does not create files",
            },
            detail="dark render stub (LongCat-Avatar int8+distill runs on the L40S box)",
        )


class QaGateAdapter(DarkAdapter):
    """QA gate stub. Deterministic pseudo-score derived from the job id.

    The real gate is W7 (wtf/qa-gate); it replaces this adapter via injection.
    """

    stage = "gate"

    @staticmethod
    def deterministic_score(job_id: str) -> float:
        digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
        return round(0.60 + (int(digest[:4], 16) / 0xFFFF) * 0.39, 4)

    def _run(self, job: Job) -> StageResult:
        score = self.deterministic_score(job.id)
        return StageResult(
            stage=self.stage,
            ok=True,
            mode="dark",
            output={
                "score": score,
                "checks": {
                    "integrity": "stub",
                    "black_frame": "stub",
                    "silence": "stub",
                    "lip_sync_wer": None,
                    "face_sim": None,
                },
                "simulated": True,
            },
            detail=f"dark gate stub (deterministic pseudo-score {score})",
        )


class PublisherAdapter(DarkAdapter):
    """Publish stage stub. Records a simulated receipt; makes zero network calls."""

    stage = "publish"

    def _run(self, job: Job) -> StageResult:
        return StageResult(
            stage=self.stage,
            ok=True,
            mode="dark",
            output={
                "receipt": f"dark-publish:{job.id}",
                "channels": [],
                "simulated": True,
                "note": "no external call made (dark stub)",
            },
            detail="dark publish stub (real channels live in wtf/publishers, approval-gated)",
        )


class _DemoFailingRender(RenderAdapter):
    """Deterministic one-shot failure used by ``factoryctl demo`` (not exported).

    Waits until the job is in ``rendering`` then reports a simulated failure.
    """

    stage = "render"

    def _run(self, job: Job) -> StageResult:
        return StageResult(
            stage=self.stage,
            ok=False,
            mode="dark",
            output={},
            detail="simulated render failure (demo scenario, dark)",
        )


#: Stage name -> adapter class (used by the pipeline and by ``factoryctl doctor``).
ADAPTER_SPEC: Dict[str, Type[DarkAdapter]] = {
    "voice": VoiceAdapter,
    "render": RenderAdapter,
    "gate": QaGateAdapter,
    "publish": PublisherAdapter,
}

DEMO_FAILING_RENDER = _DemoFailingRender


def default_adapters() -> Dict[str, DarkAdapter]:
    """Fresh dark stub instances for every required stage."""
    return {name: cls() for name, cls in ADAPTER_SPEC.items()}
