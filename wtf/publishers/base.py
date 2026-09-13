"""WTF Avatar Factory — W5 Publishers: base layer (DARK by default).

The publishers package is the only factory component allowed to talk to social
platforms. It is dark-by-default by construction:

* ``dry_run=True`` is the default on every adapter. The only transport that
  runs in that mode is :class:`DryRunTransport`, which performs zero network
  I/O and renders the exact request plan that a live call would execute.
* A live publish requires BOTH of these to hold (exact contract, no wildcards):

    1. env ``PUBLISH_LIVE`` equals exactly the channel name, and
    2. ``approvals/<channel>.json`` exists, is valid JSON, is channel-matched,
       carries an explicit expiry that has not passed, and is not an example.

* The gate is evaluated at transport entry AND immediately before every
  external call attempt (innermost layer), so revoking an approval mid-flight
  stops the very next provider call.
* No network-capable imports exist outside ``executors.py`` (where ``urllib``
  is imported lazily inside the call path). Importing this package in dark
  mode never loads the network stack at all.

This module performs no network I/O and contains no secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

CHANNELS: tuple[str, ...] = ("instagram", "youtube", "facebook", "x")
ENV_UNLOCK = "PUBLISH_LIVE"
PACKAGE_DIR = Path(__file__).resolve().parent
APPROVALS_DIR = PACKAGE_DIR / "approvals"

REQUIRED_APPROVAL_FIELDS: tuple[str, ...] = ("channel", "approved_by", "approved_at", "expires_at")
ALLOWED_APPROVAL_FIELDS: tuple[str, ...] = REQUIRED_APPROVAL_FIELDS + ("scope", "notes", "example")

STEP_CAPABILITIES: tuple[str, ...] = ("json", "binary_put", "multipart")
STEP_METHODS: tuple[str, ...] = ("GET", "POST", "PUT", "PATCH", "DELETE")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PublisherError(Exception):
    """Base error for the publishers package."""


class UnknownChannel(PublisherError):
    """Raised when a channel is not one of :data:`CHANNELS`."""


class PublishRefused(PublisherError):
    """Raised when a live publish is blocked by the dark gate."""


class CredentialMissing(PublisherError):
    """Raised when a required credential env var is absent/empty (live path)."""


class ExecutorCapabilityMissing(PublisherError):
    """Raised when an executor cannot perform a requested step capability."""


class PublishFailed(PublisherError):
    """Raised when a provider step returns non-2xx or a bounded poll times out."""


# ---------------------------------------------------------------------------
# Small utilities (no network, no side effects)
# ---------------------------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().isoformat(timespec="seconds")


def parse_iso(value: Any) -> "datetime | None":
    """Parse an ISO-8601 timestamp; require an explicit UTC offset.

    Returns None for anything that is not a tz-aware ISO-8601 string, so the
    approval validator fails closed instead of guessing a timezone.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def dig(obj: Any, path: str) -> Any:
    """Dotted-path lookup across dicts and lists ("data.0.id"). None when absent."""
    current = obj
    for part in str(path).split("."):
        if current is None:
            return None
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.lstrip("-").isdigit():
            index = int(part)
            current = current[index] if -len(current) <= index < len(current) else None
        else:
            return None
    return current


def subst(value: Any, ctx: Mapping[str, Any]) -> Any:
    """Replace ``${name}`` tokens in a string from ``ctx`` (no other templating)."""
    if isinstance(value, str) and "${" in value:
        for key, replacement in ctx.items():
            token = "${" + str(key) + "}"
            if token in value:
                value = value.replace(token, str(replacement))
    return value


def subst_deep(value: Any, ctx: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        return subst(value, ctx)
    if isinstance(value, Mapping):
        return {key: subst_deep(item, ctx) for key, item in value.items()}
    if isinstance(value, list):
        return [subst_deep(item, ctx) for item in value]
    if isinstance(value, tuple):
        return tuple(subst_deep(item, ctx) for item in value)
    return value


def capture_value(resp: Any, path: str) -> Any:
    """Read a capture target from an executor response.

    ``header:Name`` reads a response header (case-insensitive); anything else
    is treated as a dotted JSON path.
    """
    if str(path).startswith("header:"):
        wanted = str(path).split(":", 1)[1].strip().lower()
        headers = getattr(resp, "headers", None) or {}
        for key, value in dict(headers).items():
            if str(key).lower() == wanted:
                return value
        return None
    return dig(getattr(resp, "json", None), path)


def render_caption(request: "PublishRequest", *, limit: "int | None" = None) -> str:
    """Caption + hashtags, de-duplicated, optionally truncated to a limit."""
    caption = (request.caption or "").strip()
    if request.hashtags:
        missing = [tag for tag in request.hashtags if tag and ("#" + tag.lstrip("#")).lower() not in caption.lower()]
        if missing:
            tagline = " ".join("#" + tag.lstrip("#") for tag in missing)
            caption = caption + "\n\n" + tagline if caption else tagline
    if limit is not None and len(caption) > limit:
        caption = caption[: max(limit - 1, 0)].rstrip() + "\u2026"
    return caption


# ---------------------------------------------------------------------------
# Gate: env unlock + per-channel approval file (validated, expiring)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateDecision:
    channel: str
    live_allowed: bool
    reason: str
    env_ok: bool
    approval_ok: bool
    env_unlock_value: str
    approval_path: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def approval_path_for(channel: str, approvals_dir: "Path | str | None" = None) -> Path:
    base_dir = Path(approvals_dir) if approvals_dir is not None else APPROVALS_DIR
    return base_dir / (str(channel) + ".json")


def _env_value(env: Mapping[str, str], name: str) -> str:
    raw = env.get(name)
    return "" if raw is None else str(raw)


def _validate_approval_doc(data: Any, channel: str, now: datetime) -> "tuple[bool, str, str]":
    if not isinstance(data, dict):
        return False, "approval_not_object", "approval file must contain a JSON object"
    if data.get("example") is True:
        return False, "approval_example_file", "documents marked example:true never unlock a channel"
    unknown = sorted(str(key) for key in set(data) - set(ALLOWED_APPROVAL_FIELDS))
    if unknown:
        return False, "approval_unknown_fields", "unknown fields: " + ", ".join(unknown)
    missing = [name for name in REQUIRED_APPROVAL_FIELDS if name not in data]
    if missing:
        return False, "approval_missing_fields", "missing fields: " + ", ".join(missing)
    for name in REQUIRED_APPROVAL_FIELDS:
        if not isinstance(data[name], str) or not data[name].strip():
            return False, "approval_bad_field", "field " + repr(name) + " must be a non-empty string"
    if data["channel"] != channel:
        return False, "approval_channel_mismatch", "file channel " + repr(data["channel"]) + " != requested " + repr(channel)
    approved_at = parse_iso(data["approved_at"])
    expires_at = parse_iso(data["expires_at"])
    if approved_at is None or expires_at is None:
        return False, "approval_bad_timestamp", "approved_at/expires_at must be ISO-8601 with a UTC offset"
    if expires_at <= approved_at:
        return False, "approval_bad_window", "expires_at must be after approved_at"
    if expires_at <= now:
        return False, "approval_expired", "approval expired at " + data["expires_at"]
    return True, "", ""


def evaluate_gate(
    channel: str,
    *,
    approvals_dir: "Path | str | None" = None,
    env: "Mapping[str, str] | None" = None,
) -> GateDecision:
    """Read the dark gate for one channel. Pure read; no network, no writes.

    Live is allowed only when the env value equals the channel exactly AND the
    channel's approval file validates. Every other combination is blocked and
    reported with a machine-readable ``reason`` code.
    """
    env_map: Mapping[str, str] = os.environ if env is None else env
    path = approval_path_for(channel, approvals_dir)
    env_value = _env_value(env_map, ENV_UNLOCK).strip()

    reasons: list[str] = []
    detail = ""
    if channel not in CHANNELS:
        reasons.append("unknown_channel")
        detail = "unknown channel " + repr(channel) + "; expected one of " + repr(CHANNELS)

    env_ok = channel in CHANNELS and env_value == channel
    if channel in CHANNELS:
        if not env_value:
            reasons.append("env_unlock_missing")
        elif not env_ok:
            reasons.append("env_unlock_mismatch")

    approval_ok = False
    reason_code = ""
    approval_detail = ""
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            reason_code = "approval_unreadable"
            approval_detail = type(exc).__name__ + " while reading " + str(path)
        else:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                reason_code = "approval_malformed"
                approval_detail = "invalid JSON in " + str(path)
            else:
                approval_ok, reason_code, approval_detail = _validate_approval_doc(data, channel, now_utc())
    elif path.exists():
        reason_code = "approval_not_file"
        approval_detail = str(path) + " exists but is not a regular file"
    else:
        reason_code = "approval_missing"
        approval_detail = "approval file not found: " + str(path)

    if not approval_ok:
        reasons.append(reason_code or "approval_invalid")
    if not detail:
        detail = approval_detail

    live_allowed = channel in CHANNELS and env_ok and approval_ok
    reason = "" if live_allowed else "+".join(reasons) if reasons else "blocked"
    if live_allowed:
        detail = "env " + ENV_UNLOCK + "=" + repr(channel) + " and " + str(path) + " valid"
    return GateDecision(
        channel=channel,
        live_allowed=live_allowed,
        reason=reason,
        env_ok=env_ok,
        approval_ok=approval_ok,
        env_unlock_value=env_value[:64],
        approval_path=str(path),
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Credentials (names are public; values are read only on a fully-gated live call)
# ---------------------------------------------------------------------------


def resolve_credentials(
    channel: str,
    *,
    spec: "Mapping[str, str] | None" = None,
    env: "Mapping[str, str] | None" = None,
) -> dict[str, str]:
    """Resolve required credential env vars. Missing/empty => CredentialMissing.

    Error text lists env var NAMES only; values are never echoed or logged.
    """
    env_map: Mapping[str, str] = os.environ if env is None else env
    spec = dict(spec or {})
    missing = [name for name in sorted(spec) if not _env_value(env_map, name).strip()]
    if missing:
        raise CredentialMissing(
            "missing required credential env var(s) for channel "
            + repr(channel)
            + ": "
            + ", ".join(missing)
            + " (set them on the deploy box only; never store them in files)"
        )
    return {name: _env_value(env_map, name).strip() for name in spec}


def bearer_auth_builder(token_env: str) -> "Callable[..., Mapping[str, str]]":
    """Build a header-builder that emits ``Authorization: Bearer <token>``."""

    def _builder(creds: Mapping[str, str], step: "PublishStep", *, url: str, query: Mapping[str, Any], form_fields: Mapping[str, str]) -> Mapping[str, str]:
        token = creds.get(token_env, "")
        if not token:
            raise CredentialMissing("credential " + token_env + " missing for live auth header")
        return {"Authorization": "Bearer " + token}

    return _builder


def _no_auth_builder(creds: Mapping[str, str], step: "PublishStep", *, url: str, query: Mapping[str, Any], form_fields: Mapping[str, str]) -> Mapping[str, str]:
    raise PublisherError("no auth header builder wired for this channel")


# ---------------------------------------------------------------------------
# Request / plan / receipt models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishRequest:
    channel: str
    job_id: str
    title: str
    caption: str
    video_path: str
    hashtags: tuple = ()
    visibility: str = "public"
    extra: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> "PublishRequest":
        if self.channel not in CHANNELS:
            raise UnknownChannel("unknown channel " + repr(self.channel) + "; expected one of " + repr(CHANNELS))
        if not str(self.job_id).strip():
            raise PublisherError("job_id is required")
        if not str(self.video_path).strip():
            raise PublisherError("video_path is required")
        if self.visibility not in ("public", "unlisted", "private"):
            raise PublisherError("visibility must be public|unlisted|private, got " + repr(self.visibility))
        return self


@dataclass(frozen=True)
class PublishStep:
    name: str
    method: str
    url: str
    query: Mapping[str, Any] = field(default_factory=dict)
    json_body: "Mapping[str, Any] | None" = None
    binary_file: "str | None" = None
    multipart: "tuple[Mapping[str, Any], ...] | None" = None
    headers: Mapping[str, str] = field(default_factory=dict)
    capture: "Mapping[str, str] | None" = None
    poll: "Mapping[str, Any] | None" = None
    capability: str = "json"

    def validate(self) -> None:
        if self.capability not in STEP_CAPABILITIES:
            raise PublisherError("step " + repr(self.name) + ": unknown capability " + repr(self.capability))
        if str(self.method).upper() not in STEP_METHODS:
            raise PublisherError("step " + repr(self.name) + ": unsupported method " + repr(self.method))
        if self.poll is not None:
            for key in ("field", "until"):
                if key not in self.poll:
                    raise PublisherError("step " + repr(self.name) + ": poll requires " + repr(key))


@dataclass(frozen=True)
class PublishPlan:
    channel: str
    job_id: str
    steps: "tuple[PublishStep, ...]"
    summary: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> "PublishPlan":
        if self.channel not in CHANNELS:
            raise UnknownChannel("plan channel " + repr(self.channel))
        if not self.steps:
            raise PublisherError("plan must contain at least one step")
        for step in self.steps:
            step.validate()
        return self

    @property
    def final_step(self) -> PublishStep:
        return self.steps[-1]

    def digest(self) -> str:
        """Stable sha256 over the executable plan (never includes secrets)."""
        payload = {
            "channel": self.channel,
            "job_id": self.job_id,
            "steps": [
                {
                    "name": step.name,
                    "method": str(step.method).upper(),
                    "url": step.url,
                    "query": step.query,
                    "json_body": step.json_body,
                    "binary_file": step.binary_file,
                    "multipart": step.multipart,
                    "headers": step.headers,
                    "capture": step.capture,
                    "poll": step.poll,
                    "capability": step.capability,
                }
                for step in self.steps
            ],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PublishReceipt:
    channel: str
    job_id: str
    dry_run: bool
    accepted: bool
    transport: str
    provider_post_id: "str | None"
    endpoint: str
    http_method: str
    steps_planned: int
    steps_executed: int
    payload_digest: str
    created_at: str
    gate_reason: str
    notes: tuple = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class Transport(ABC):
    name: str = "transport"
    dry_run: bool = True

    @abstractmethod
    def deliver(self, plan: PublishPlan, *, request: "PublishRequest | None" = None) -> PublishReceipt:
        raise NotImplementedError


class DryRunTransport(Transport):
    """Offline transport: renders the exact plan, performs ZERO network I/O."""

    name = "dry-run"
    dry_run = True

    def __init__(self, *, gate_reason: str = "dry_run_default") -> None:
        self._gate_reason = gate_reason

    def deliver(self, plan: PublishPlan, *, request: "PublishRequest | None" = None) -> PublishReceipt:
        digest = plan.digest()
        final = plan.final_step
        return PublishReceipt(
            channel=plan.channel,
            job_id=plan.job_id,
            dry_run=True,
            accepted=False,
            transport=self.name,
            provider_post_id="dry-run-" + digest[:12],
            endpoint=final.url,
            http_method=str(final.method).upper(),
            steps_planned=len(plan.steps),
            steps_executed=0,
            payload_digest=digest,
            created_at=iso_now(),
            gate_reason=self._gate_reason,
            notes=(
                "dry-run: zero network calls; no provider request was made",
                "steps planned: " + str(len(plan.steps)),
            ),
        )


class LiveTransport(Transport):
    """Gated live transport.

    Refuses unless env unlock + approval file both pass. The gate is re-checked
    immediately before EVERY external call attempt (innermost layer), so an
    approval revoked mid-flight stops the next call. The executor is
    injectable; the default stdlib executor is imported lazily only when a
    fully-gated live call actually runs.
    """

    name = "live"
    dry_run = False

    def __init__(
        self,
        *,
        executor: "Callable[..., Any] | None" = None,
        sleeper: "Callable[[float], None] | None" = None,
        approvals_dir: "Path | str | None" = None,
        env: "Mapping[str, str] | None" = None,
        credential_spec: "Mapping[str, str] | None" = None,
        header_builder: "Callable[..., Mapping[str, str]] | None" = None,
    ) -> None:
        self._executor = executor
        self._sleeper = sleeper or time.sleep
        self._approvals_dir = approvals_dir
        self._env = env
        self._credential_spec = credential_spec or {}
        self._header_builder = header_builder or _no_auth_builder

    # -- innermost gate -----------------------------------------------------

    def _guard(self) -> None:
        decision = evaluate_gate(self._current_channel, approvals_dir=self._approvals_dir, env=self._env)
        if not decision.live_allowed:
            raise PublishRefused(
                "live publish refused for " + repr(self._current_channel) + ": " + decision.reason + " (" + decision.detail + ")"
            )

    def _resolved_executor(self) -> "Callable[..., Any]":
        if self._executor is None:
            # Lazy by design: dark mode never even imports the network stack.
            from executors import StdlibHttpExecutor

            self._executor = StdlibHttpExecutor(guard=self._guard)
        return self._executor

    # -- delivery -----------------------------------------------------------

    def deliver(self, plan: PublishPlan, *, request: "PublishRequest | None" = None) -> PublishReceipt:
        self._current_channel = plan.channel
        decision = evaluate_gate(plan.channel, approvals_dir=self._approvals_dir, env=self._env)
        if not decision.live_allowed:
            raise PublishRefused(
                "live publish refused for " + repr(plan.channel) + ": " + decision.reason + " (" + decision.detail + ")"
            )
        credentials = resolve_credentials(plan.channel, spec=self._credential_spec, env=self._env)
        executor = self._resolved_executor()

        ctx: dict[str, Any] = {}
        executed = 0
        last_resp: Any = None
        for step in plan.steps:
            attempts = 1
            if step.poll:
                attempts = max(1, int(step.poll.get("max_attempts", 1)))
            for attempt in range(1, attempts + 1):
                self._guard()  # innermost kill-switch re-check: immediately before the call
                url = str(subst(step.url, ctx))
                query = {key: subst(value, ctx) for key, value in dict(step.query).items()}
                form_fields: dict[str, str] = {}
                if step.multipart:
                    for entry in step.multipart:
                        if "value" in entry:
                            form_fields[str(entry.get("name", ""))] = str(subst(entry.get("value"), ctx))
                headers = {key: str(value) for key, value in dict(self._header_builder(credentials, step, url=url, query=query, form_fields=form_fields)).items()}
                headers.update({key: str(subst(value, ctx)) for key, value in dict(step.headers).items()})
                resp = executor(
                    method=str(step.method).upper(),
                    url=url,
                    headers=headers,
                    query=query,
                    json_body=subst_deep(step.json_body, ctx),
                    binary_file=str(subst(step.binary_file, ctx)) if step.binary_file else None,
                    multipart=subst_deep(step.multipart, ctx),
                    capability=step.capability,
                )
                executed += 1
                last_resp = resp
                status = int(getattr(resp, "status_code", 0))
                if not 200 <= status < 300:
                    raise PublishFailed("step " + repr(step.name) + " failed: HTTP " + str(status))
                if step.poll:
                    value = dig(getattr(resp, "json", None), step.poll["field"])
                    if value == step.poll["until"]:
                        break
                    if attempt >= attempts:
                        raise PublishFailed(
                            "poll step " + repr(step.name) + " timed out after " + str(attempts) + " attempts"
                        )
                    self._sleeper(float(step.poll.get("interval_s", 1.0)))
            if step.capture and last_resp is not None:
                for key, path in step.capture.items():
                    value = capture_value(last_resp, path)
                    if value in (None, "", [], {}):
                        raise PublishFailed(
                            "capture " + repr(key) + ": path " + repr(path) + " missing in step " + repr(step.name)
                        )
                    ctx[key] = value
        provider_post_id = None if ctx.get("post_id") is None else str(ctx["post_id"])
        final = plan.final_step
        return PublishReceipt(
            channel=plan.channel,
            job_id=plan.job_id,
            dry_run=False,
            accepted=True,
            transport=self.name,
            provider_post_id=provider_post_id,
            endpoint=final.url,
            http_method=str(final.method).upper(),
            steps_planned=len(plan.steps),
            steps_executed=executed,
            payload_digest=plan.digest(),
            created_at=iso_now(),
            gate_reason="env+approval ok (" + decision.approval_path + ")",
            notes=("live path executed via injected/default executor",),
        )


# ---------------------------------------------------------------------------
# Channel adapter base
# ---------------------------------------------------------------------------


class ChannelAdapter(ABC):
    """One adapter per channel: builds the plan, applies the gate, returns receipts."""

    channel: str = ""
    credential_spec: Mapping[str, str] = {}
    auth_token_env: "str | None" = None
    max_caption_chars: "int | None" = None

    def __init__(
        self,
        *,
        dry_run: bool = True,
        approvals_dir: "Path | str | None" = None,
        env: "Mapping[str, str] | None" = None,
        executor: "Callable[..., Any] | None" = None,
        sleeper: "Callable[[float], None] | None" = None,
    ) -> None:
        self.dry_run = bool(dry_run)
        self.approvals_dir = approvals_dir
        self.env = env
        self.executor = executor
        self.sleeper = sleeper

    @abstractmethod
    def plan(self, request: PublishRequest) -> PublishPlan:
        raise NotImplementedError

    def build_auth_headers(self, credentials: Mapping[str, str], step: PublishStep, *, url: str, query: Mapping[str, Any], form_fields: Mapping[str, str]) -> Mapping[str, str]:
        if not self.auth_token_env:
            raise PublisherError(self.channel + ": no auth header builder wired")
        builder = bearer_auth_builder(self.auth_token_env)
        return builder(credentials, step, url=url, query=query, form_fields=form_fields)

    def publish(self, request: PublishRequest) -> PublishReceipt:
        request.validate()
        if request.channel != self.channel:
            raise UnknownChannel(
                "request channel " + repr(request.channel) + " != adapter channel " + repr(self.channel)
            )
        plan = self.plan(request).validate()
        decision = evaluate_gate(self.channel, approvals_dir=self.approvals_dir, env=self.env)
        if self.dry_run:
            if decision.live_allowed:
                reason = "gate_open_but_dry_run_default (" + decision.approval_path + ")"
            else:
                reason = decision.reason
            return DryRunTransport(gate_reason=reason).deliver(plan)
        transport = LiveTransport(
            executor=self.executor,
            sleeper=self.sleeper,
            approvals_dir=self.approvals_dir,
            env=self.env,
            credential_spec=self.credential_spec,
            header_builder=self.build_auth_headers,
        )
        return transport.deliver(plan)


def adapter_registry() -> "dict[str, type[ChannelAdapter]]":
    """Channel -> adapter class. Imported lazily to avoid import cycles."""
    import facebook_page
    import instagram_graph
    import x_api
    import youtube_shorts

    return {
        "instagram": instagram_graph.InstagramGraphAdapter,
        "youtube": youtube_shorts.YouTubeShortsAdapter,
        "facebook": facebook_page.FacebookPageAdapter,
        "x": x_api.XApiAdapter,
    }
