"""LLM provider interface for the W4 Content Brain.

Design contract (v1, DARK):
- The LLM interface is STUBBED: ``StubProvider`` generates every content piece
  deterministically from curated slots (no model, no randomness, no I/O).
- ``live`` is ``False`` for every provider that can actually be constructed,
  except ``LiveProvider`` which is deliberately NOT WIRED: requesting it
  requires ``CONTENT_BRAIN_LIVE=1`` and even then ``generate()`` refuses.
- This module never imports a network client. Enforced by tests (sockets are
  hard-disabled in the whole test suite) and by self_audit.py.

A future live integration must land behind ``LiveProvider.generate`` only
after an explicit outbound-approval change, and must keep the same request /
response contract used by the stub so brain.py stays untouched.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

KINDS = (
    "hook",
    "context",
    "points",
    "proof",
    "cta",
    "caption",
    "hashtags",
    "thumbnail",
    "shot_list",
)

LIVE_ENV_FLAG = "CONTENT_BRAIN_LIVE"

_SHOT_TIME_RE = r"^\d+:\d{2}–\d+:\d{2}$"


class ProviderError(RuntimeError):
    """Raised when a provider is misconfigured or asked for invalid input."""


class LiveDisabled(ProviderError):
    """Live mode was requested while the environment is dark (default)."""


class LiveNotWired(ProviderError):
    """Live mode is enabled but the v1 interface is intentionally stubbed."""


class _SafeDict(dict):
    """format_map helper: unknown placeholders render as empty string.

    Prevents KeyError on optional slots; callers validate required content
    explicitly instead of failing on template mechanics.
    """

    def __missing__(self, key):  # noqa: D105 - trivial
        return ""


def render_template(text: str, slots: Dict[str, Any]) -> str:
    """Render a ``{slot}`` template with safe (non-throwing) substitution."""
    return text.format_map(_SafeDict(slots))


@dataclass(frozen=True)
class GenerationRequest:
    """One content-generation request.

    ``prompt_template`` / ``prompt_text`` are carried for auditability: the
    stub ignores them when producing deterministic output, but every real
    call records what would have been sent to a model.
    """

    kind: str
    slots: Dict[str, Any]
    prompt_template: str = ""
    prompt_text: str = ""
    variation: int = 0


class LLMProvider(ABC):
    name = "abstract"
    live = False

    @abstractmethod
    def generate(self, request: GenerationRequest) -> str:
        """Return the generated text for ``request.kind`` (JSON for list kinds)."""


def _pick(options: Sequence[Any], variation: int) -> Any:
    if not isinstance(options, (list, tuple)) or not options:
        raise ProviderError("empty option list for selection")
    return options[int(variation) % len(options)]


def _require_str(slots: Dict[str, Any], key: str) -> str:
    value = slots.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderError("slot {!r} must be a non-empty string".format(key))
    return value


def _require_str_list(slots: Dict[str, Any], key: str, minimum: int = 1) -> List[str]:
    value = slots.get(key)
    if not isinstance(value, list) or len(value) < minimum:
        raise ProviderError(
            "slot {!r} must be a list with >= {} items".format(key, minimum)
        )
    if not all(isinstance(v, str) and v.strip() for v in value):
        raise ProviderError("slot {!r} must contain non-empty strings".format(key))
    return value


class StubProvider(LLMProvider):
    """Deterministic, offline content generator (the v1 default).

    Every piece is derived from the curated ``slots`` - rotating through the
    provided option lists by ``variation`` - so identical requests produce
    byte-identical output and the daily brief is fully reproducible.
    """

    name = "stub"
    live = False

    def generate(self, request: GenerationRequest) -> str:
        if request.kind not in KINDS:
            raise ProviderError("unknown generation kind: {!r}".format(request.kind))
        builder = getattr(self, "_build_" + request.kind)
        return builder(request)

    # -- builders -----------------------------------------------------------

    def _build_hook(self, request: GenerationRequest) -> str:
        slots = request.slots
        template = _pick(_require_str_list(slots, "hooks"), request.variation)
        return render_template(template, slots)

    def _build_context(self, request: GenerationRequest) -> str:
        slots = request.slots
        template = _pick(_require_str_list(slots, "contexts"), request.variation)
        return render_template(template, slots)

    def _build_points(self, request: GenerationRequest) -> str:
        points = _require_str_list(request.slots, "points", minimum=3)
        return json.dumps(points, ensure_ascii=False)

    def _build_proof(self, request: GenerationRequest) -> str:
        return _require_str(request.slots, "proof")

    def _build_cta(self, request: GenerationRequest) -> str:
        slots = request.slots
        template = _pick(_require_str_list(slots, "ctas"), request.variation)
        return render_template(template, slots)

    def _build_caption(self, request: GenerationRequest) -> str:
        slots = request.slots
        lines = [_require_str(slots, "title"), _require_str(slots, "angle"),
                 _require_str(slots, "cta_line")]
        return "\n".join(lines)

    def _build_hashtags(self, request: GenerationRequest) -> str:
        slots = request.slots
        pool = _require_str_list(slots, "hashtag_pool", minimum=1)
        for tag in pool:
            if not tag.startswith("#"):
                raise ProviderError("hashtag without '#': {!r}".format(tag))
        cap = int(slots.get("max_hashtags", 12))
        seen: List[str] = []
        for tag in pool:
            if tag not in seen:
                seen.append(tag)
        return json.dumps(seen[:cap], ensure_ascii=False)

    def _build_thumbnail(self, request: GenerationRequest) -> str:
        slots = request.slots
        concept = _pick(_require_str_list(slots, "concepts"), request.variation)
        thumb = _require_str(slots, "thumb_text")
        expr = _require_str(slots, "expression")
        return (
            "Concept: {}. Overlay text: “{}”. Expression: {}. "
            "Style: horizontal-safe first frame, WTF maroon #8B0000 + gold #C9A227, "
            "no stock-photo look.".format(concept, thumb, expr)
        )

    def _build_shot_list(self, request: GenerationRequest) -> str:
        beats = request.slots.get("beats")
        if not isinstance(beats, list) or len(beats) < 2:
            raise ProviderError("slot 'beats' must be a list with >= 2 shots")
        import re

        for beat in beats:
            if not isinstance(beat, dict):
                raise ProviderError("each beat must be a mapping")
            missing = {"t", "type", "shot", "overlay"} - set(beat)
            if missing:
                raise ProviderError("beat missing keys: {}".format(sorted(missing)))
            if not re.match(_SHOT_TIME_RE, str(beat["t"])):
                raise ProviderError("beat timecode malformed: {!r}".format(beat["t"]))
            if not str(beat["shot"]).strip() or not str(beat["overlay"]).strip():
                raise ProviderError("beat shot/overlay must be non-empty")
        return json.dumps(beats, ensure_ascii=False)


class LiveProvider(LLMProvider):
    """Interface stub for the future live LLM path (NOT WIRED in v1).

    Constructing this class requires an explicit opt-in
    (``CONTENT_BRAIN_LIVE=1``); calling ``generate`` still refuses, because
    the v1 content brain ships dark. Wiring a real client here is a separate,
    approval-gated change.
    """

    name = "live"
    live = True

    def generate(self, request: GenerationRequest) -> str:
        raise LiveNotWired(
            "Live LLM generation is not wired in v1 (interface stub). "
            "Keep CONTENT_BRAIN_LIVE unset; wire an approved client before enabling."
        )


def get_provider(
    name: str = "stub",
    *,
    live: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> LLMProvider:
    """Return a provider. Dark by default - live mode must be asked for AND allowed."""
    env = dict(os.environ) if env is None else env
    if live:
        if env.get(LIVE_ENV_FLAG) != "1":
            raise LiveDisabled(
                "live mode requires {}='1' (dark default); refusing.".format(LIVE_ENV_FLAG)
            )
        return LiveProvider()
    if name in ("stub", "default"):
        return StubProvider()
    raise ProviderError("unknown provider: {!r}".format(name))
