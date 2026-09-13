"""W5 Publishers — HTTP executors.

This is the ONLY module in the package permitted to import network-capable
modules, and only lazily inside the call path (asserted by an AST scan in
``tests/test_zero_network.py``). Two hard properties:

* ``StdlibHttpExecutor`` refuses to run without a gate-guard callable, and the
  guard is invoked IMMEDIATELY before any network I/O — the innermost layer of
  the dark gate.
* ``multipart`` is intentionally NOT wired in v1 (dark). Channels whose plans
  need it (X chunked upload) fail closed with ``ExecutorCapabilityMissing``
  until a deploy-time executor is provided. This is a documented boundary, not
  an accident.

Network errors (DNS/TLS/timeouts) propagate as stdlib exceptions; that is
fail-closed behavior — nothing is retried automatically.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from base import ExecutorCapabilityMissing

SUPPORTED_CAPABILITIES: "tuple[str, ...]" = ("json", "binary_put")


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    json: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)
    text: "str | None" = None


def _json_or_none(text: str) -> Any:
    try:
        return _json.loads(text)
    except (ValueError, TypeError):
        return None


class StdlibHttpExecutor:
    """Minimal stdlib executor (json + binary_put). Deploy-time wiring point."""

    def __init__(self, *, guard: "Callable[[], None] | None" = None, timeout_s: float = 60.0) -> None:
        self._guard = guard
        self.timeout_s = float(timeout_s)

    def __call__(
        self,
        *,
        method: str,
        url: str,
        headers: "Mapping[str, str] | None" = None,
        query: "Mapping[str, Any] | None" = None,
        json_body: "Mapping[str, Any] | None" = None,
        binary_file: "str | None" = None,
        multipart: "Any" = None,
        capability: str = "json",
    ) -> HttpResponse:
        if capability not in SUPPORTED_CAPABILITIES:
            raise ExecutorCapabilityMissing(
                "capability "
                + repr(capability)
                + " is not wired in the v1 stdlib executor; wire a multipart-capable executor "
                "at deploy before enabling this channel live"
            )
        if multipart:
            raise ExecutorCapabilityMissing(
                "multipart payload supplied to a non-multipart capability; refusing to guess encoding"
            )
        if self._guard is None:
            raise RuntimeError("StdlibHttpExecutor refuses to run without a gate guard callable")
        self._guard()

        # Network stack imported lazily, AFTER the gate re-check.
        import urllib.error
        import urllib.parse
        import urllib.request

        query = dict(query or {})
        params: dict[str, str] = {}
        for key, value in query.items():
            if value is None:
                continue
            if isinstance(value, bool):
                params[str(key)] = "true" if value else "false"
            else:
                params[str(key)] = str(value)
        target = str(url) + (("?" + urllib.parse.urlencode(params)) if params else "")

        data = None
        header_map = {str(key): str(value) for key, value in dict(headers or {}).items()}
        if binary_file is not None:
            data = Path(str(binary_file)).read_bytes()
        elif json_body is not None:
            data = _json.dumps(json_body).encode("utf-8")
            if not any(key.lower() == "content-type" for key in header_map):
                header_map["Content-Type"] = "application/json"

        request = urllib.request.Request(target, data=data, method=str(method).upper())
        for key, value in header_map.items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = response.read().decode("utf-8", "replace")
                return HttpResponse(
                    status_code=int(getattr(response, "status", 0) or 0),
                    json=_json_or_none(body),
                    headers={str(key): str(value) for key, value in dict(getattr(response, "headers", {}) or {}).items()},
                    text=body,
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if exc.fp else ""
            return HttpResponse(
                status_code=int(exc.code or 0),
                json=_json_or_none(body),
                headers={str(key): str(value) for key, value in dict(getattr(exc, "headers", {}) or {}).items()},
                text=body,
            )
