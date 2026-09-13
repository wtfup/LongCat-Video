"""Shared test helpers. No network, no real credentials (placeholders only)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from base import PublishRequest, adapter_registry
from executors import HttpResponse


def credential_env_names(channel: str) -> tuple:
    """The credential env names an adapter requires (single source of truth)."""
    return tuple(sorted(adapter_registry()[channel].credential_spec))


def env_for(channel: str, **extra) -> dict:
    """Credential env with obvious placeholders; never real secrets."""
    env = {name: "TEST-PLACEHOLDER-" + name for name in credential_env_names(channel)}
    env.update(extra)
    return env


def approval_doc(channel: str, *, approved_minutes_ago: int = 10, expires_in_minutes: int = 120, **overrides):
    now = datetime.now(timezone.utc)
    doc = {
        "channel": channel,
        "approved_by": "Test Operator (placeholder)",
        "approved_at": (now - timedelta(minutes=approved_minutes_ago)).isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(minutes=expires_in_minutes)).isoformat(timespec="seconds"),
        "scope": "publish:avatar-short",
    }
    doc.update(overrides)
    return doc


def write_approval(directory, channel: str, **overrides) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (channel + ".json")
    path.write_text(json.dumps(approval_doc(channel, **overrides)), encoding="utf-8")
    return path


def make_request(channel: str, tmp_path=None, **overrides) -> PublishRequest:
    base_dir = Path(tmp_path) if tmp_path is not None else Path("/tmp")
    kwargs = {
        "channel": channel,
        "job_id": "job-0001",
        "title": "Test title",
        "caption": "Test caption",
        "video_path": str(base_dir / "render.mp4"),
    }
    kwargs.update(overrides)
    return PublishRequest(**kwargs)


class FakeExecutor:
    """Records every executor call and returns queued responses. ZERO network.

    Queue entries: an HttpResponse, a dict (auto-wrapped as a 200 JSON body),
    or a callable receiving the call kwargs.
    """

    def __init__(self, responses=None, hook=None) -> None:
        self.calls: list = []
        self.responses: list = list(responses or [])
        self.hook = hook

    def __call__(self, **kwargs) -> HttpResponse:
        self.calls.append(kwargs)
        if self.hook is not None:
            self.hook(kwargs, len(self.calls))
        if self.responses:
            response = self.responses.pop(0)
            if callable(response):
                return response(**kwargs)
            if isinstance(response, HttpResponse):
                return response
            return HttpResponse(status_code=200, json=response, headers={})
        return HttpResponse(status_code=200, json={"id": "fake-post-id"}, headers={})


class RecordingSleeper:
    """Instant no-op sleeper that records requested intervals."""

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, seconds) -> None:
        self.calls.append(seconds)
