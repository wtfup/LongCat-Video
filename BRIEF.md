# BRIEF — WTF Avatar Factory v1 (kanban swarm worker contract)

**Repo root:** `/Users/vishalnigammacminioffice/wtf-avatar-factory` (WTF fork of LongCat-Video, branch `wtf-factory`)
**Read first:** `/Users/vishalnigammacminioffice/wtf-avatar-factory/PLAN.md`
**All factory code lands under:** `/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/`

## Mission

Build the v1 factory packages (W1–W7 below) so the parent can deploy them to the AWS pilot box. The repo already contains the upstream LongCat-Video code — your code goes in NEW directories under `wtf/`. Never modify upstream files.

## Shared interface contract (schema v1 — do not deviate)

SQLite DB path: `/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db`

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

Status flow: `queued → rendering → rendered → gated → approved | rejected → published` (failures → `failed`).
W1 owns this schema and documents it; W2 reads/writes via the same DB path; W7 writes `gate_score`/`gate_json`.

## Worker deliverables (find your row by ID)

| ID | Title | Package dir (absolute) | Headline deliverables | Evidence dir |
|----|-------|------------------------|----------------------|--------------|
| W1 | Factory Core — queue + orchestrator | `wtf/factory-core/` | `factory_core/` (jobqueue.py, pipeline.py, adapters.py stubs with DARK flags, cli.py `factoryctl`), tests (pytest green), README.md (schema + usage), demo log | `wtf/factory-core/evidence/` |
| W2 | Console v1 — approval UI | `wtf/console/` | `app.py` (FastAPI: GET /jobs, /jobs/{id}, POST /jobs, /jobs/{id}/approve, /jobs/{id}/reject, GET /health), `static/` SPA (clean premium look, WTF maroon #8B0000 + gold #C9A227 accents, queue + preview grid + approve/reject), tests (TestClient), README.md (run: uvicorn app:app --port 8795) | `wtf/console/evidence/` |
| W3 | Render Kit — deploy + benchmark | `wtf/render-kit/` | `setup_l40s.sh`, `download_weights.sh` (exact HF ids), `run_avatar.py` (int8 + distill + cp=1 for L40S), `benchmark.py` (writes bench_receipt.json; `--self-test` offline), `models.md`, `RUNBOOK.md` | `wtf/render-kit/evidence/` |
| W4 | Content Brain — scripts + calendar | `wtf/content-brain/` | `brain.py` (daily briefs: hook, 45s script ×2 languages, b-roll shot list, caption+hashtags, thumbnail idea), `pillars.yaml` (AI systems, founder journey, WTF brands, fitness/transformation, business), `calendar.py`, `providers.py` (LLM interface STUBBED, live=False), prompts/, 10 sample briefs, tests | `wtf/content-brain/evidence/` |
| W5 | Publishers — DARK adapters | `wtf/publishers/` | `base.py` (Transport + DryRunTransport), adapters: instagram_graph.py, youtube_shorts.py, facebook_page.py, x_api.py — all `dry_run=True` default; live refused unless env `PUBLISH_LIVE=<channel>` AND `approvals/<channel>.json` present; tests prove ZERO network; `APPROVALS.md` (exact per-channel credential + approval spec) | `wtf/publishers/evidence/` |
| W6 | Capture Kit — recording + intake | `wtf/capture-kit/` | `SHOT_LIST.md` (exact spec: 4K/1080p, 3 outfits, 2 angles, lighting, Hindi 2-min + English 2-min monologue + 10-min casual talk, 15s neutral clip per outfit), `CAPTURE_CHECKLIST.md`, `intake.py` (organize raw footage → assets/{refs,voice,face_clips}, ffprobe validation, --dry-run), `voice_requirements.md` | `wtf/capture-kit/evidence/` |
| W7 | QA Gate — realism scoring | `wtf/qa-gate/` | `gate.py` (checks: ffprobe integrity, black-frame, silence; lip-sync WER via faster-whisper IFACE stubbed; face-sim IFACE stubbed; best-of-N selector), `policy.yaml` thresholds, tests with synthetic good/bad fixtures proving correct accept/reject | `wtf/qa-gate/evidence/` |

## Hard rules (all workers)

1. **Absolute paths only** (workers run in a scratch cwd). Write ONLY inside your package dir + your evidence dir. Never modify the upstream repo code or other workers' dirs.
2. **NO git commands.** The parent commits/pushes. NO AWS calls. NO live/external API calls — everything dark/stubbed. Do not download model weights (the runbook covers that on the box).
3. **No secrets** anywhere; redact anything sensitive as `prefix6…[REDACTED]`.
4. Tests must run with `python -m pytest` using only stdlib + pinned deps (add `requirements.txt` per package). If pip install is unavailable in your env, write code + requirements, run what you can (`python -m py_compile`, `bash -n`), and report honestly what could not execute.
5. Every main deliverable doc (README/RUNBOOK/SHOT_LIST) ends with a `## 5. Self-audit` section listing: writes count, live calls (must be 0), secrets printed (0), unverified claims (0), or the exact deviation.
6. **Completion contract:** copy your headline deliverable files to `$HERMES_KANBAN_WORKSPACE/` root before declaring done (resolver maps basenames there). Done = your self-audit prints 0 failures + tests green + evidence written.
7. Report blockers honestly — a truthful "not executed" beats a fabricated receipt.

## Done criteria for the swarm

All W1–W7 deliverables exist at the exact paths above; every package's test suite green on fresh execution; verifier re-executes independently (and secret-scans deliverables + logs); synthesizer writes the rollup with per-item gap arithmetic to `wtf/_evidence/SYNTHESIS.md`; parent then commits + pushes `wtf-factory` and deploys to the pilot box.
