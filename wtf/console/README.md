# W2 — Console v1 (approval UI)

Local approval console for the WTF Avatar Factory queue. Vishal opens it once
per day, reviews the QA-gated renders, and approves or rejects each clip in
under five minutes. Built as work package **W2** of the Avatar Factory v1
swarm (`BRIEF.md` row W2).

* **Framework:** FastAPI (serves both the JSON API and the SPA)
* **Storage:** the shared factory SQLite DB — schema v1, owned by W1
  (`/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db`)
* **UI:** dependency-free SPA (`static/`), WTF maroon `#8B0000` + gold
  `#C9A227` on dark ivory, serif display type
* **Posture:** dark by construction — zero network calls, zero external
  resources, zero credentials in this package

## 1. Deliverable map

| File | Purpose |
|------|---------|
| `app.py` | FastAPI app factory (`create_app`) + module app for uvicorn |
| `db.py` | SQLite access layer: schema v1 DDL, job CRUD, atomic approve/reject, guarded render resolution |
| `static/index.html` | SPA shell: stats, queue grid, preview drawer, new-job modal |
| `static/styles.css` | Premium dark theme (maroon/gold), responsive |
| `static/app.js` | Vanilla JS: filters, polling, approve/reject, toasts (all output escaped) |
| `tests/conftest.py` | Ensures `import app` works from any pytest cwd |
| `tests/test_console.py` | 25 TestClient tests incl. adversarial + zero-network proof |
| `requirements.txt` | Pinned deps (fastapi, uvicorn, httpx, pytest) |
| `self_audit.py` | Exit-code self-audit (deliverables, docs, secrets, pytest) |
| `evidence/run_evidence.py` | End-to-end evidence run against an isolated demo DB |
| `evidence/*.txt`, `evidence/demo/` | Generated transcripts + demo DB (isolated from the canonical DB) |

## 2. Run

```bash
cd /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/console
python -m pip install -r requirements.txt      # fastapi / uvicorn / httpx / pytest
python -m uvicorn app:app --port 8795 --host 127.0.0.1
```

Open `http://127.0.0.1:8795/` on the box, or port-forward it over SSM:
`aws ssm start-session --target <instance-id> --document-name AWS-StartPortForwardingSession --parameters '{"portNumber":["8795"],"localPortNumber":["8795"]}'`.

Environment (optional overrides):

| Variable | Default |
|----------|---------|
| `WTF_FACTORY_DB` | `/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db` |
| `WTF_MEDIA_ROOT` | `/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/renders` |

The DB directory is created on first request, so the console can bootstrap a
fresh box. The canonical DB is only touched by the running console — tests and
demos always use isolated paths.

**Security note (v1):** the console has no login. It must stay bound to
`127.0.0.1` / the private VPN — never expose port 8795 publicly. The pilot box
already has zero public ingress; SSM port-forwarding is the intended access
path. (Auth is a Phase-5 item, not part of this package.)

## 3. HTTP API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness + DB status + per-status counts (`503` when DB unusable) |
| `GET` | `/jobs` | Queue listing. Filters: `status`, `brand`, `limit` (1–500, default 100), `offset` |
| `GET` | `/jobs/{id}` | Job detail + full event history + render availability |
| `POST` | `/jobs` | Queue a job. Body: `brand`, `pillar`, `language` required; `title`, `script`, `id` optional → `201` |
| `POST` | `/jobs/{id}/approve` | `gated` → `approved`. Idempotent replay → `changed:false`. Other states → `409` |
| `POST` | `/jobs/{id}/reject` | `gated` → `rejected`. Optional body `{"reason": "..."}` appended to `notes`. Same idempotency/409 rules |
| `GET` | `/jobs/{id}/render` | Streams the local render file, **only** when it resolves inside the media root |
| `GET` | `/` | SPA. `/static/*` assets. `/docs` OpenAPI UI |

Status flow (schema v1, W1-owned): `queued → rendering → rendered → gated →
approved | rejected → published` (failures → `failed`). Decisions are only
valid from **`gated`** — the QA gate (W7) is what moves a render into review,
exactly mirroring the `factory-core` state machine (`TRANSITIONS[gated] =
{approved, rejected, failed}`).

Transitions are recorded in `events` using W1's vocabulary:
`{"event": "status_change", "meta": {"from": "gated", "to": "approved",
"by": "console", "reason": ...}}`. Job creation logs `{"event": "created"}`.

Error shapes: `404 {"detail":{"error":"not_found"}}`,
`409 {"detail":{"error":"invalid_transition","current":...,"allowed_from":["gated"]}}`,
`422` validation (pydantic), `503 {"detail":{"error":"db_unavailable",...}}`.

**Render serving is fail-closed.** `GET /jobs/{id}/render` only serves files
that resolve (symlinks included) inside `WTF_MEDIA_ROOT`; `..` traversal,
symlink escapes, remote URLs and unresolvable paths are refused (`403`/`404`).
The console never proxies remote files.

## 4. Design & operations notes

* **DB compatibility:** `db.py` carries the exact schema-v1 DDL
  (`CREATE TABLE IF NOT EXISTS`), so the console works standalone against a
  fresh DB and converges on the same file W1 orchestrates. W2 only writes
  `jobs` rows it creates plus the decision `status_change` event; `gate_score`/
  `gate_json` are W7's write path and are displayed read-only.
* **Decisions are atomic:** `decide()` runs `BEGIN IMMEDIATE` … `COMMIT` with a
  status re-check inside the transaction, so a concurrent pipeline write can
  never be clobbered; replays are idempotent (`changed:false`).
* **UI behaviour:** 15 s polling; status chips (Needs review first), brand
  filter, search; drawer shows the video player, QA score + gate JSON, script,
  notes and the full audit timeline. Every DB-sourced string is HTML-escaped
  before it reaches the DOM.
* **Dark defaults:** no telemetry, no fonts/CDN, no analytics, no outbound
  calls of any kind. `#8B0000` / `#C9A227` are the only accent colors.
* **Tests:** `python -m pytest` (25 tests) — API contract, validation,
  transitions incl. idempotency and 409s, render guarding (traversal, symlink
  escape, ENAMETOOLONG), SPA presence, schema conformance vs the BRIEF DDL,
  env-var override, and a socket-blocked zero-network proof.

## 5. Self-audit

Audited 2026-09-13 by the W2 worker (FITTY) against BRIEF.md row W2.

* **Writes:** 11 authored files in this package — `app.py`, `db.py`,
  `static/{index.html,styles.css,app.js}`, `tests/{conftest.py,test_console.py}`,
  `requirements.txt`, `self_audit.py`, `evidence/run_evidence.py`, `README.md` —
  plus generated evidence transcripts/artifacts under `evidence/` (demo DB
  isolated in `evidence/demo/`). Nothing outside `wtf/console/` was written;
  the canonical shared DB at `wtf/_data/factory.db` was never created or
  touched by any test, demo or audit run (all runs use explicit tmp/evidence
  paths).
* **Live calls: 0.** No git, no AWS, no outbound HTTP(S). Proven three ways:
  (a) `tests/test_console.py::test_zero_network_calls` blocks
  `socket.connect` / `connect_ex` / `create_connection` / `getaddrinfo` and
  runs a full request cycle anyway; (b) self-audit check 5 proves `app.py` /
  `db.py` import no network modules; (c) self-audit check 3 proves the static
  assets contain zero external references. The evidence run repeats the
  socket-block proof end-to-end (phase 9).
* **Secrets printed: 0.** Self-audit check 4 (AWS keys, private-key blocks,
  provider token patterns, assigned passwords, bearer literals) scans every
  authored text file in the package — 0 hits. No credential material exists
  anywhere in this package; nothing needed redaction.
* **Unverified claims: 0.** Every statement above is re-executed by the
  reproduce commands below; transcripts are in `evidence/`.
* **Deviation: none.** (The only additions beyond the literal W2 contract are
  additive read/UI endpoints — `GET /`, `GET /jobs/{id}/render` — required for
  the required "preview grid" to function; documented in section 3.)

Reproduce:

```bash
cd /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/console
python -m pytest                    # 25 passed
python self_audit.py                # SELF-AUDIT FAILURES: 0
python evidence/run_evidence.py     # FAILURES: 0
```

Evidence artifacts: `evidence/tests_fresh_run.txt`,
`evidence/self_audit.txt`, `evidence/demo_flow_output.txt`,
`evidence/demo/` (demo DB + fake render clips).
