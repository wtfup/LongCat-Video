"""W5 Publishers — X (Twitter) adapter (dark v1).

Publish flow (X API v2 chunked media upload + create post; executed only when
the dark gate passes at both layers):

  1. POST /2/media/upload  multipart command=INIT      -> data.id captured as media_id
  2. POST /2/media/upload  multipart command=APPEND    (segment; Content-Range when size known)
  3. POST /2/media/upload  multipart command=FINALIZE  (processing-state poll is deploy wiring)
  4. POST /2/tweets        JSON {text, media.media_ids}

Auth is OAuth 1.0a user-context HMAC-SHA1, built here with stdlib only. The
signing key never appears in headers or receipts. Chunked segment splits and
FINALIZE processing-state polling are deploy wiring details; the v1 dark plan
models a single segment. Zero network I/O; this module only builds plans.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from pathlib import Path
from typing import Any, Mapping

from base import ChannelAdapter, PublishPlan, PublishRequest, PublishStep, PublisherError, render_caption

X_API = "https://api.x.com"
POST_LIMIT = 280

_UNRESERVED_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def rfc3986_quote(value: Any) -> str:
    """RFC 3986 percent-encoding, equivalent to ``urllib.parse.quote(s, safe="")``.

    Implemented locally on purpose: the package must import zero urllib
    modules outside the guarded executor (enforced by the AST scan in
    tests/test_zero_network.py).
    """
    data = str(value).encode("utf-8")
    return "".join(chr(byte) if byte in _UNRESERVED_BYTES else "%%%02X" % byte for byte in data)


def oauth1_authorization_header(
    *,
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    extra_params: "Mapping[str, Any] | None" = None,
    nonce: "str | None" = None,
    timestamp: "str | None" = None,
) -> dict[str, str]:
    """Build an OAuth 1.0a Authorization header (HMAC-SHA1).

    Deterministic when ``nonce``/``timestamp`` are supplied (tests use that).
    ``consumer_secret``/``token_secret`` are used only as the signing key and
    never appear in the header itself.
    """
    nonce = nonce or secrets.token_hex(16)
    timestamp = timestamp or str(int(time.time()))
    oauth_params = {
        "oauth_consumer_key": str(consumer_key),
        "oauth_nonce": str(nonce),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp),
        "oauth_token": str(token),
        "oauth_version": "1.0",
    }
    params: dict[str, str] = dict(oauth_params)
    for key, value in dict(extra_params or {}).items():
        if value is None:
            continue
        params[str(key)] = str(value)
    encoded = "&".join(
        rfc3986_quote(key) + "=" + rfc3986_quote(value) for key, value in sorted(params.items())
    )
    base_url = str(url).split("?", 1)[0]
    base_string = "&".join([str(method).upper(), rfc3986_quote(base_url), rfc3986_quote(encoded)])
    signing_key = rfc3986_quote(consumer_secret) + "&" + rfc3986_quote(token_secret)
    signature = base64.b64encode(
        hmac.new(signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")
    oauth_params["oauth_signature"] = signature
    header = "OAuth " + ", ".join(
        rfc3986_quote(key) + '="' + rfc3986_quote(value) + '"' for key, value in sorted(oauth_params.items())
    )
    return {"Authorization": header}


def _file_size(path: str) -> int:
    try:
        return int(Path(str(path)).stat().st_size)
    except OSError:
        return 0


class XApiAdapter(ChannelAdapter):
    channel = "x"
    credential_spec = {
        "X_CONSUMER_KEY": "X app API key (OAuth 1.0a consumer key)",
        "X_CONSUMER_SECRET": "X app API secret (OAuth 1.0a consumer secret)",
        "X_ACCESS_TOKEN": "User-context access token for the posting account",
        "X_ACCESS_TOKEN_SECRET": "User-context access token secret",
    }
    max_caption_chars = POST_LIMIT

    def plan(self, request: PublishRequest) -> PublishPlan:
        if request.visibility != "public":
            raise PublisherError("x: only visibility='public' is supported by this adapter")
        text = (str(request.title).strip() + "\n\n" + render_caption(request)).strip()
        if len(text) > POST_LIMIT:
            text = text[: POST_LIMIT - 1].rstrip() + "\u2026"
        size = _file_size(request.video_path)
        append_headers: dict[str, str] = {}
        if size > 0:
            append_headers["Content-Range"] = "bytes 0-" + str(size - 1) + "/" + str(size)
        steps = (
            PublishStep(
                name="init_upload",
                method="POST",
                url=X_API + "/2/media/upload",
                multipart=(
                    {"name": "command", "value": "INIT"},
                    {"name": "media_type", "value": "video/mp4"},
                    {"name": "total_bytes", "value": str(size)},
                ),
                capability="multipart",
                capture={"media_id": "data.id"},
            ),
            PublishStep(
                name="append_upload",
                method="POST",
                url=X_API + "/2/media/upload",
                multipart=(
                    {"name": "command", "value": "APPEND"},
                    {"name": "media_id", "value": "${media_id}"},
                    {"name": "segment_index", "value": "0"},
                    {"name": "media", "file": request.video_path},
                ),
                headers=append_headers,
                capability="multipart",
            ),
            PublishStep(
                name="finalize_upload",
                method="POST",
                url=X_API + "/2/media/upload",
                multipart=(
                    {"name": "command", "value": "FINALIZE"},
                    {"name": "media_id", "value": "${media_id}"},
                ),
                capability="multipart",
            ),
            PublishStep(
                name="create_post",
                method="POST",
                url=X_API + "/2/tweets",
                json_body={"text": text, "media": {"media_ids": ["${media_id}"]}},
                capture={"post_id": "data.id"},
            ),
        )
        return PublishPlan(
            channel=self.channel,
            job_id=request.job_id,
            steps=steps,
            summary={
                "surface": "x_post",
                "text_chars": len(text),
                "video_bytes": size,
                "chunking": "single-segment APPEND in v1; segment/range headers filled at deploy",
                "processing_poll": "FINALIZE processing-state polling is deploy wiring",
            },
        )

    def build_auth_headers(
        self,
        credentials: Mapping[str, str],
        step: PublishStep,
        *,
        url: str,
        query: Mapping[str, Any],
        form_fields: Mapping[str, str],
    ) -> Mapping[str, str]:
        return oauth1_authorization_header(
            method=step.method,
            url=url,
            consumer_key=credentials.get("X_CONSUMER_KEY", ""),
            consumer_secret=credentials.get("X_CONSUMER_SECRET", ""),
            token=credentials.get("X_ACCESS_TOKEN", ""),
            token_secret=credentials.get("X_ACCESS_TOKEN_SECRET", ""),
            extra_params={**dict(query), **dict(form_fields)},
        )
