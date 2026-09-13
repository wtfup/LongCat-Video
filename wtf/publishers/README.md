# W5 Publishers — DARK adapters (WTF Avatar Factory v1)

Package path: `wtf/publishers/` (in the WTF fork `wtfup/LongCat-Video`, branch
`wtf-factory`). This is work package **W5** of the Avatar Factory swarm: the
only component that may ever talk to social platforms, dark by construction.

Contract reference: `BRIEF.md` (W5 row) and `PLAN.md` §8 (gates & safety).

## 1. What is in here

| File | Purpose |
|------|---------|
| `base.py` | `PublishRequest`/`PublishStep`/`PublishPlan`/`PublishReceipt`, the dark gate (`evaluate_gate`), `Transport` + `DryRunTransport` + `LiveTransport`, `ChannelAdapter`, credential resolver, adapter registry |
| `executors.py` | `StdlibHttpExecutor` — the ONLY module allowed to import the network stack, lazily, behind a mandatory gate guard. `multipart` capability is refused by design (deploy wiring point) |
| `instagram_graph.py` | Instagram Reels adapter (Graph API v21.0): create container -> poll FINISHED -> media_publish |
| `youtube_shorts.py` | YouTube Shorts adapter (Data API v3 resumable upload): init session -> PUT bytes |
| `facebook_page.py` | Facebook Page adapter (Graph API v21.0): `POST /{page-id}/videos` |
| `x_api.py` | X adapter (API v2 chunked upload + `POST /2/tweets`), OAuth 1.0a HMAC-SHA1 signing, stdlib-only RFC3986 encoder |
| `cli.py` | `publisherctl` — offline operator surface (`gate-report`, `dry-run`, `adapters`) |
| `approvals/` | the per-channel unlock surface + `example.approval.json` (can never unlock) |
| `tests/` | 135 tests: gate matrix, transport paths, adapter plan shapes, ZERO-network proofs |
| `evidence/` | re-execution receipts for the verifier |

## 2. Safety model (the whole point of W5)

Live publishing requires **BOTH** keys, and only then does anything network
capable load at all:

1. `PUBLISH_LIVE=<channel>` exported on the deploy box — exact channel name,
   no wildcards (`all`, `*`, `true`, comma lists, case variants never unlock).
2. `approvals/<channel>.json` present and valid: channel-matched, explicit
   `approved_by`, ISO-8601 `approved_at`/`expires_at` **with UTC offset**,
   `expires_at` in the future, not `example:true`, no unknown fields.

Enforcement layering (defense in depth, every layer re-reads live state):

```
adapter.publish()            # dry_run=True default -> DryRunTransport (zero I/O)
  -> LiveTransport.deliver() # entry gate check; refuses before anything else
     -> per-attempt guard    # immediately before EVERY provider call attempt
        -> StdlibHttpExecutor(guard=...)  # innermost: refuses without the guard,
                                          # re-checks, THEN lazily imports urllib
```

Revocation semantics: approvals are re-read immediately before every call, so
deleting `approvals/<channel>.json` stops the next call even mid-flight.

Failure is always fail-closed: refusal raises `PublishRefused`; missing
credentials raise `CredentialMissing` (names only, never values); unsupported
capabilities raise `ExecutorCapabilityMissing`; non-2xx and poll timeouts
raise `PublishFailed`. Nothing retries automatically.

## 3. Usage (all offline)

```bash
cd /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/publishers

python cli.py gate-report            # dark-gate truth table, all channels
python cli.py gate-report --json     # machine-readable
python cli.py adapters --json        # adapters + credential env names

python cli.py dry-run \
  --channel instagram --job-id job-0001 \
  --title "AI stack day 1" --caption "What we shipped" \
  --video /abs/path/render.mp4 --hashtags AI,WTF
```

Programmatic (what factory-core will call):

```python
from base import PublishRequest, adapter_registry

adapter = adapter_registry()["instagram"]()          # dry_run=True default
receipt = adapter.publish(PublishRequest(
    channel="instagram", job_id="job-0001",
    title="...", caption="...", video_path="/abs/render.mp4",
))
assert receipt.dry_run is True and receipt.steps_executed == 0
```

Integration note: this package deliberately does **not** touch
`wtf/_data/factory.db`. The queue/state machine (`queued -> ... ->
published`) is owned by W1 factory-core; W5 returns receipts, factory-core
records status.

## 4. Zero-network guarantees (how they are proven)

Three independent layers, all re-executed by the verifier:

1. **Socket tripwire** (`tests/conftest.py`, autouse): every test replaces
   `socket.socket` / `create_connection` / `getaddrinfo` / `ssl.wrap_socket`
   with recording guards; each test asserts 0 attempts (the positive control
   proves the tripwire is armed). A forgotten sleeper cannot stall the suite —
   wall-clock sleeps are no-ops in tests.
2. **Subprocess module check**: importing the package and dry-running all
   four adapters adds NO network-capable module (`socket`, `ssl`,
   `http.client`, `urllib.request`, `urllib.error`, `requests`, `httpx`,
   `aiohttp`) to the interpreter.
3. **AST source scan**: no package module imports a network module at module
   level; function-level network imports exist ONLY in `executors.py`.

```bash
python -m pytest -v          # 135 passed
```

## 5. Self-audit

- Writes (runtime): 0 — the package performs no filesystem writes; receipts
  are return values / stdout only.
- Files authored: see `evidence/checksums.sha256` (files are confined to
  `wtf/publishers/`).
- Live calls: 0 — zero sockets across the full test suite (socket tripwire
  asserted on every test; see `evidence/zero_network_report.json`).
- Secrets printed: 0 — no credential values anywhere; tests use
  `TEST-PLACEHOLDER-*` strings; error messages list env var names only.
- Unverified claims: 0. Exact deviation: endpoint/payload shapes and OAuth1
  signing follow each platform's published API documentation and standard
  RFC 5849 rules, and were **not** executed against live APIs (dark build —
  live calls are forbidden and gated). Deploy-time smoke testing against
  sandbox/test accounts is required before the first approved publish window;
  X chunked-upload segmenting and FINALIZE processing-polling are explicitly
  marked as deploy wiring in `x_api.py` and `APPROVALS.md`.
