# factory-core (W1) — Factory Core: job queue + orchestrator

Part of the WTF Avatar Factory v1 (`/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/`).
This package is the **spine**: the shared SQLite job queue (schema v1), the stage
orchestrator, dark stage-adapter stubs, and the `factoryctl` operator CLI.

Everything here is **dark by default**: zero network calls, zero external APIs,
stdlib-only at runtime. Real stages (LongCat-Avatar render, TTS, W7 QA gate, W5
publishers) plug in later by implementing one interface and being injected.

---

## 1. Overview

```
factoryctl add ─► jobs (queued) ─► factoryctl run ─► rendering ─► rendered ─► gated
                                                            (W7 writes gate_score/gate_json)
Console (W2) approve/reject ─► approved ─► factoryctl publish ─► published
                              └ rejected                       (W5 publishers replace the stub)
any stage error ─────────────────► failed ─► factoryctl requeue ─► queued
```

| Component | File | Role |
|---|---|---|
| Queue | `factory_core/jobqueue.py` | schema v1, state machine, events, optimistic locking |
| Orchestrator | `factory_core/pipeline.py` | drives stages, failure handling, approvals, publish |
| Stage stubs | `factory_core/adapters.py` | dark `voice/render/gate/publish` adapters (zero network) |
| CLI | `factory_core/cli.py` | `factoryctl` (also `python -m factory_core.cli`) |

## 2. Schema v1 — the shared contract

**Database path (do not change):**

```
/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db
```

```sql
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  brand TEXT NOT NULL,
  pillar TEXT NOT NULL,
  language TEXT NOT NULL,
  title TEXT,
  script TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  render_path TEXT,
  gate_score REAL,
  gate_json TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  event TEXT NOT NULL,
  meta TEXT
);
```

**Status flow (enforced by the queue, not by convention):**

```
queued → rendering → rendered → gated → approved | rejected → published
(any active status → failed on error; failed → queued only via an explicit requeue)
```

- `id`: `job_<12 hex>` (server-generated).
- `created_at` / `ts`: ISO-8601 UTC (`...+00:00`).
- `queued → approved` (or any skipped hop) is **refused** — `InvalidTransition`.
- `failed` may only be reached from an active status; terminal jobs are immutable.
- FIFO is **insertion order** (`rowid`), not wall-clock — same-second jobs stay ordered.

**Events (audit trail; W2 and W7 read this):** `created`, `status_change`
(meta: `from`,`to`,`reason`), `stage_start`, `stage_done` (meta: `stage`,`mode`,
`duration_s`,`detail`), `stage_error` (meta: `stage`,`error`). The pipeline and the
queue write them; nothing else should insert event rows.

**Concurrency:** WAL journal + 5 s busy timeout, so the console (W2) can read while
the pipeline writes. Every transition is an optimistic `UPDATE ... WHERE id=? AND
status=<observed>` — a stale writer gets `ConcurrentUpdateError` instead of
clobbering a newer state.

**Integration for other packages (W2 console / W7 gate):**

```python
import sys
sys.path.insert(0, "/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/factory-core")
from factory_core.jobqueue import FactoryDB

db = FactoryDB()                      # creates the schema at the contract path if missing
for job in db.list_jobs(status="gated"):
    ...                               # preview
db.transition(job_id, "approved", reason="console approve")
db.record_gate(job_id, score, detail) # this is W7's write path for gate_score/gate_json
```

W7 may instead implement the same `run(job) -> StageResult` interface used by the
stubs and inject it: `Pipeline(db, adapters={"gate": RealGate(), ...})`.

## 3. Usage

Runtime is **Python 3.9+ stdlib only** — no installation required:

```bash
cd /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/factory-core

python -m factory_core.cli doctor          # environment + dark-flag report
python -m factory_core.cli init-db         # create schema at the contract path
python -m factory_core.cli add --brand "WTF Gyms" --pillar fitness-transformation \
    --language hindi --title "Transformation Tuesday"
python -m factory_core.cli run             # drain queued jobs -> gated
python -m factory_core.cli approve <job>   # or: reject <job> --notes "..."
python -m factory_core.cli publish         # approved -> published (dark stub)
python -m factory_core.cli list --json ; python -m factory_core.cli stats
```

Installing the console script (optional): `pip install -e .` → then `factoryctl <cmd>`.
All commands accept `--db <path>` (default = the contract path above).

| Command | Purpose |
|---|---|
| `init-db` | create schema-v1 tables (idempotent) |
| `add` | queue a job (`--json`, `--script`, `--script-file` absolute path) |
| `list` / `show` / `events` | inspect queue, job detail, audit trail (`--json` available) |
| `run` | advance queued jobs through render/gate (`--until rendered\|gated`, `--once`, `--limit`) |
| `approve` / `reject` | operator decision on a gated job (`--notes`) |
| `publish` | publish approved jobs via the dark publisher stub |
| `requeue` | explicit `failed → queued` retry (`--reason`) |
| `stats` | per-status counts |
| `doctor` | python/db/dark-flag report (never mutates anything) |
| `demo` | offline end-to-end scenario, writes `evidence/demo.log` + `evidence/demo_receipt.json` |

Exit codes: `0` ok · `1` unexpected · `2` argparse usage · `3` domain error
(not found / illegal transition / live access refused).

**Adapter contract** (`factory_core/adapters.py`): `run(job) -> StageResult(stage,
ok, mode, output, detail, duration_s)`. `DarkAdapter(dark=False)` raises
`LiveAccessRefused` — the stubs cannot be reconfigured into live mode by accident.
Stage failures (`ok=False`) or exceptions mark the job `failed` with the reason in
the event trail; the loop never crashes.

## 4. Verification

```bash
# 63 tests — re-run this exact command to reproduce:
uv run --no-project --with pytest python -m pytest

# also verified on the system Python 3.9.6:
uv run --no-project --with pytest --python /usr/bin/python3 python -m pytest

# offline end-to-end demo (writes evidence/demo.log, exits non-zero on any failed check):
python -m factory_core.cli demo
```

What the suite proves (63 tests, all green on 3.11.14 **and** 3.9.6):

- **Schema exactness** — stored DDL in `sqlite_master` is byte-equal to the BRIEF DDL
  (normalized whitespace / canonical SQLite form), column order/types/constraints,
  AUTOINCREMENT, zero-filled counts, WAL + busy timeout.
- **State machine** — every legal transition, every illegal hop refused
  (`InvalidTransition`), terminal jobs immutable, fail idempotency, explicit requeue,
  optimistic-concurrency conflict detected (`ConcurrentUpdateError`).
- **Pipeline** — happy path to `gated` with full event trail; `until=rendered`;
  drain limits; adapter `ok=False` and adapter *exceptions* both contained and
  recorded; gate-without-score fails closed; publish touches only `approved`;
  publish failure handled; requeue → retry; validation of adapters/limits.
- **Dark guarantees** — `DARK=True`, `LIVE_ENABLED=False`, live construction refused,
  stub side effects absent, AST scan finds **no network-capable imports** and no
  third-party imports anywhere in `factory_core/`.
- **Zero-network E2E** — the full lifecycle *and* the CLI demo run with
  `socket.socket` / `getaddrinfo` / `HTTP(S)Connection` / `urlopen` patched to
  raise: any egress attempt fails the test (`tests/test_no_network.py`).
- **CLI** — every command, JSON output, exit codes (0/2/3), `python -m` invocation
  in a subprocess, demo artifacts (log ends with the self-audit block, receipt
  `status=SUCCESS`, final statuses `published×2 / rejected / gated`).

**Evidence** (`evidence/`): `pytest_output.txt` (63 passed, py3.11),
`pytest_output_py39.txt` (63 passed, py3.9.6), `demo.log` (full scenario + checks +
self-audit), `demo_receipt.json` (machine receipt), `demo.db` (demo run state —
never the shared contract DB).

## 5. Self-audit

- writes: 16 package files (4 root + 6 `factory_core/` + 6 `tests/`) + 6 evidence
  artifacts (`demo.log`, `demo_receipt.json`, `demo.db`, `run_receipt.json`,
  `pytest_output.txt`, `pytest_output_py39.txt`) — all inside `wtf/factory-core/`;
  upstream LongCat-Video code, other workers' dirs, and the shared DB path were
  never touched.
- live calls: 0 — dark stubs only; the full lifecycle and demo ran under a
  patched-socket guard; AST scan finds no network-capable imports.
- secrets printed: 0 — no credentials exist anywhere in this package.
- unverified claims: 0 — every runnable claim above names its command; test counts
  are from fresh executions on 3.11.14 and 3.9.6; demo checks are computed live.
- deviations: none. (`evidence/demo.db` is intentionally test-run state inside the
  evidence dir; the contract DB is only created by `init-db` or first real use.)
