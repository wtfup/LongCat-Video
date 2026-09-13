# APPROVALS.md — per-channel credential + approval specification

W5 Publishers, WTF Avatar Factory v1. This is the exact operator spec for
enabling a live publish on any channel. If you are activating a channel, read
this document top to bottom first — nothing here is optional.

## 0. The two keys (global rules)

No publish ever happens live unless **both** keys are present at the moment of
the call:

1. **Env unlock**: `PUBLISH_LIVE` equals the exact channel name
   (`instagram` | `youtube` | `facebook` | `x`). Exact match only. `all`,
   `*`, `true`, `1`, comma lists, and case variants (`Instagram`) do **not**
   unlock. Surrounding whitespace is stripped; nothing else is.
2. **Approval file**: `wtf/publishers/approvals/<channel>.json` exists and
   validates (see §3). One env value unlocks at most one channel.

Both keys are re-read immediately before **every** provider call attempt.
Deleting the approval file mid-flight stops the next call. There is no
in-memory approval, no cached unlock, and no CLI flag that bypasses either
key. All four adapters default to `dry_run=True`, which performs zero network
I/O regardless of gate state.

## 1. Per-channel credential spec (names are public; values live ONLY in the deploy box env)

Never store credential values in any file in this repository — env only.
Missing/empty values fail closed with `CredentialMissing` (the error lists
names, never values).

### Instagram (`instagram_graph.py`)

| Env var | What it must be |
|---------|-----------------|
| `IG_GRAPH_ACCESS_TOKEN` | Long-lived Instagram Graph API access token from a Meta app with **instagram_basic**, **instagram_content_publish** (and **pages_show_list** for setup), tied to the IG Business account |
| `IG_USER_ID` | The IG Business account id used in `/{ig-user-id}/...` graph paths |

Flow: `POST /v21.0/{IG_USER_ID}/media` (`media_type=REELS`, `video_url`,
`caption`, `share_to_feed=true`) -> poll `GET /{container-id}?fields=status_code`
until `FINISHED` (bounded: 30 attempts x 10s in the plan) -> `POST
/v21.0/{IG_USER_ID}/media_publish` (`creation_id`). Requires `video_url` to be
a public HTTPS URL at live time (the factory render host, e.g.
S3/CloudFront); if `extra["video_url"]` is absent the local `video_path` is
rendered into the plan verbatim as a reminder. Public visibility only.

### YouTube (`youtube_shorts.py`)

| Env var | What it must be |
|---------|-----------------|
| `YT_OAUTH_ACCESS_TOKEN` | OAuth 2.0 access token with the **youtube.upload** scope for the target channel (refresh handled at deploy) |
| `YT_CHANNEL_ID` | Target channel id (audit trail; the upload binds to the token identity) |

Flow: `POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`
-> session URI arrives in the **Location response header** (captured) -> `PUT`
the rendered MP4 bytes (`binary_put`). Title gets `#Shorts` (kept <=100
chars); `privacyStatus` maps 1:1 from the request visibility
(public|unlisted|private).

### Facebook (`facebook_page.py`)

| Env var | What it must be |
|---------|-----------------|
| `FB_PAGE_ACCESS_TOKEN` | Facebook Page access token with **pages_manage_posts** and **pages_read_engagement** |
| `FB_PAGE_ID` | The Page id used in `/{page-id}/videos` |

Flow: `POST /v21.0/{FB_PAGE_ID}/videos` with `file_url` (public HTTPS URL at
live time), `description`, `title`, `published`. `published=true` only for
`visibility=public`; `unlisted`/`private` render as a Page draft
(`published=false`).

### X (`x_api.py`)

| Env var | What it must be |
|---------|-----------------|
| `X_CONSUMER_KEY` | X app API key (OAuth 1.0a consumer key) |
| `X_CONSUMER_SECRET` | X app API secret (OAuth 1.0a consumer secret) |
| `X_ACCESS_TOKEN` | User-context access token for the posting account |
| `X_ACCESS_TOKEN_SECRET` | User-context access token secret |

Flow: chunked media upload via `POST /2/media/upload` (INIT -> APPEND ->
FINALIZE, `multipart`) then `POST /2/tweets` (`text` <=280 chars,
`media.media_ids`). Auth is OAuth 1.0a HMAC-SHA1, computed per request; the
signing key is used only as the HMAC key and never appears in headers,
receipts, or logs.

**Deploy wiring boundary (explicit):** the v1 stdlib executor supports `json`
and `binary_put` only. X's upload steps need `multipart`, which the stdlib
executor **refuses** (`ExecutorCapabilityMissing`). Wire a multipart-capable
executor at deploy before enabling X live; also fill in chunk segmentation for
large files and FINALIZE processing-state polling. Until then X refuses
closed even with both keys.

## 2. Approval file format

Path is exactly `approvals/<channel>.json` (example:
`approvals/instagram.json`). It is operator-privileged, non-secret state:
never commit real approvals (`.gitignore` excludes `approvals/*.json`).

```json
{
  "channel": "instagram",
  "approved_by": "Vishal Nigam",
  "approved_at": "2026-09-13T18:00:00+05:30",
  "expires_at": "2026-09-20T18:00:00+05:30",
  "scope": "publish:avatar-short"
}
```

| Field | Required | Rule |
|-------|----------|------|
| `channel` | yes | must equal the channel being published |
| `approved_by` | yes | non-empty string (accountability) |
| `approved_at` | yes | ISO-8601 **with UTC offset** |
| `expires_at` | yes | ISO-8601 with UTC offset, after `approved_at`, strictly in the future |
| `scope` | no | free string for the audit trail |
| `notes` | no | free string |
| `example` | no | if `true`, the file can NEVER unlock (misfire guard) |

Unknown fields are refused (typo guard). `approvals/example.approval.json`
ships as a template: it is both wrongly-named for the gate and marked
`example:true`, so it can never unlock anything.

## 3. Gate reason codes (what a refusal says)

Refusals join failing checks with `+`, e.g.
`env_unlock_missing+approval_missing`. Codes: `env_unlock_missing`,
`env_unlock_mismatch`, `approval_missing`, `approval_not_file`,
`approval_unreadable`, `approval_malformed`, `approval_not_object`,
`approval_example_file`, `approval_unknown_fields`, `approval_missing_fields`,
`approval_bad_field`, `approval_channel_mismatch`, `approval_bad_timestamp`,
`approval_bad_window`, `approval_expired`, `unknown_channel`.

## 4. Activation checklist (deploy box)

1. Install runtime (stdlib only) and confirm tests: `python -m pytest`
   (must be green; 135 tests).
2. Export the channel's credential env vars (see §1) in the service
   environment — never in a file.
3. Write `approvals/<channel>.json` with a bounded expiry (§2).
4. Export `PUBLISH_LIVE=<channel>`.
5. `python cli.py gate-report` -> exactly one channel must show
   `live_allowed True`; the other three must stay blocked.
6. `python cli.py dry-run --channel <channel> ...` -> inspect the receipt
   (digest, endpoint, steps).
7. First live action: publish one test artifact to the real channel and
   confirm the provider post id in the receipt matches the platform's UI.
8. Revocation: `rm approvals/<channel>.json` and unset `PUBLISH_LIVE`.
   Re-run `gate-report` to confirm 0/4.

Rotation policy: rotate tokens on the platform side and update env; approvals
expire on their own schedule and must be renewed deliberately. Treat the
approvals directory as privileged operator state.

## 5. Self-audit

- Live calls made by this package during the build: 0 (dark; enforced by the
  socket tripwire on every test and by the AST scan — see
  `evidence/zero_network_report.json`).
- Secrets in files: 0 — this document names env vars and scopes only;
  placeholder strings only in tests.
- Unverified claims: 0 live-verified behavior claims. Exact deviation:
  endpoint shapes/scopes follow the platforms' published documentation; they
  were NOT executed against live APIs (forbidden in this build), so §4 step 7
  (one watched test publish) is mandatory before volume, and X needs its
  multipart executor wired first (§1, X).
