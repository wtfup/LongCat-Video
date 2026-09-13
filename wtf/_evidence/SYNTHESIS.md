# SYNTHESIS — WTF Avatar Factory v1 (W1–W7 rollup)

**Synthesizer task:** `t_a9265b75` · **Swarm root:** `t_3d039441` · **Verifier:** `t_c150c232` (gate PASS)
**Written:** 2026-09-13 17:28  on Hermes-Prod-Mini · **Repo:** `/Users/vishalnigammacminioffice/wtf-avatar-factory` (fork `wtfup/LongCat-Video`, branch `wtf-factory`)

## 1. Verdict

Swarm complete and independently verified. The code baseline is ready for the parent to commit/push
`wtf-factory` and run the pilot deploy checklist in §4. No blocking gaps; every remaining item is a
deploy-time or Phase-2 action, not a code defect.

- 7/7 packages (W1–W7) delivered; all 45 BRIEF-listed deliverable path checks present (files + tests + evidence dirs).
- 428/428 tests green on fresh execution: verifier re-ran all 7 suites (10 runs, incl. py3.9.6 + uv cross-version), and the synthesizer re-ran all 7 suites fresh (all exit 0) before writing this rollup.
- 313 files secret-scanned → 0 hits. W5 dark proof: 29/29 probe checks, 0 package socket attempts, 30/30 checksums.
- Main docs carry a real `## 5. Self-audit` block (verifier: 14/14; synthesizer scan: 15 files incl. evidence indexes — 0 missing). Upstream repo files untouched. Errata: 0. Fixes applied by verifier: 0.

## 2. Per-item deliverable status

| ID | Package (dir under `wtf/`) | Headline deliverables (all present at exact path) | Tests, synthesizer fresh re-run | Self-audit | Workspace copy |
|----|---------------------------|--------------------------------------------------|--------------------------------|------------|----------------|
| W1 | `factory-core/` | `factory_core/{jobqueue,pipeline,adapters,cli}.py` + README (schema/usage) + evidence demo log | **63 passed** | PASS | 8 files |
| W2 | `console/` | `app.py` (FastAPI), `static/` SPA (maroon #8B0000 + gold #C9A227), tests (TestClient) + README | **25 passed** | PASS | 14 files |
| W3 | `render-kit/` | `setup_l40s.sh`, `download_weights.sh` (exact HF ids), `run_avatar.py` (int8+distill+cp=1), `benchmark.py`, `models.md`, `RUNBOOK.md` | **42 passed** | PASS (3 docs) | 6 files |
| W4 | `content-brain/` | `brain.py` (bilingual 45s briefs), `pillars.yaml` (5 pillars × 2 topics), `calendar.py` (+ enqueue), `providers.py` (LLM stub, live=False), `prompts/` (7), `samples/` (10 briefs + sha256 index), tests | **70 passed** | PASS | 12 items |
| W5 | `publishers/` | `base.py` + 4 adapters (`instagram_graph`, `youtube_shorts`, `facebook_page`, `x_api`) all dark by default, `APPROVALS.md`, tests proving zero network | **135 passed** | PASS (2 docs) | 17 files incl. manifest |
| W6 | `capture-kit/` | `SHOT_LIST.md` (4K/1080p, 3 outfits, 2 angles, Hindi+English monologue), `CAPTURE_CHECKLIST.md`, `intake.py` (ffprobe-validated, --dry-run), `voice_requirements.md` | **31 passed** | PASS (4 docs) | 5 files |
| W7 | `qa-gate/` | `gate.py` (ffprobe integrity, black-frame, silence, stubbed WER/face-sim IFACEs, best-of-N), `policy.yaml`, tests with synthetic good/bad fixtures | **72 passed** | PASS | 4 files |

Totals: **7/7 packages · 45/45 path checks · 428/428 tests · 0 missing · 0 secrets · 0 errata.**

Evidence index per package: `wtf/<pkg>/evidence/` (W1: demo.log/receipts; W3: 12 artifacts; W5: 9 artifacts incl. gate matrices + zero-network report + checksums; W6: 8; W7: 17). Verifier pack: `wtf/_evidence/VERIFICATION.md` + `.json`, probe receipt in the verifier workspace `verify_logs/publishers_probe_receipt.json`.

## 3. Gap arithmetic & interface notes

Contract surface (what exists vs what a deploy still needs — nothing here blocks the commit):

- **DB spine:** schema v1 exact (jobs + events only) at `wtf/_data/factory.db`. Intentionally NOT created by the build → deploy step 4 runs `factoryctl init-db`. W2/W4/W7 share the same `FactoryDB` import path; concurrency is WAL + optimistic UPDATE (safe for console alongside pipeline).
- **Pipeline stages:** voice/render/gate/publish adapters ship as dark stubs; live wiring contract is `Pipeline(db, adapters={...})` / `run(job) -> StageResult`. Deploy-time work (not a gap in v1 scope): TTS voice clone, render adapter on the box, W7 live scorers (faster-whisper lip-sync WER, face-sim), W5 live executors.
- **W5 dark gate:** live refused unless `PUBLISH_LIVE == '<channel>'` (exact match) AND valid `approvals/<channel>.json`; re-checked immediately before every provider call. Known deploy boundary: **X live needs a multipart-capable executor + chunk segmentation**; all other channels are credential+approval only. LinkedIn stays manual, always.
- **W7 gate semantics:** writes `gate_score`/`gate_json`; verdict accept→`gated` / reject→`rejected`; run exit codes 0/2/1; stub metrics never fabricate (unavailable+required ⇒ reject).
- **W4→queue:** `python calendar.py --days N --enqueue --db <factory.db>` — one queued job per language, idempotent; job notes carry brief_id/topic/brand/format metadata.
- **W3 throughput table:** intentionally unlocked — fill from the first measured `bench_receipt.json` on the L40S (no invented numbers shipped; `--self-test` receipts are marked synthetic).
- **Orchestrator artifacts (keep-vs-clean before commit):** repo-root `army_dashboard.html`, `wtf/_ops/` (swarm dashboard tooling), 9 `.pytest_cache/` dirs (pytest self-ignores their contents via an inner `.gitignore` with `*`, but cleaning is cheap hygiene). `*.mp4` and `weights/` are already gitignored; `wtf/_data/` is absent and must stay so.
- **Blocked on humans (not on code):** Vishal's 30–45 min capture session (Phase 2) and the Phase-4 blind panel → GO/NO-GO.

## 4. Parent deploy checklist

0. **Pre-commit hygiene** — decide keep vs clean: `army_dashboard.html`, `wtf/_ops/`, 9 `.pytest_cache/` dirs (`find . -name .pytest_cache -prune -exec rm -rf {} +` if cleaning). Do not add `wtf/_data/` or large media.
1. **Commit + push** branch `wtf-factory` (workers ran zero git by contract; this is the parent's step).
2. **Pilot infra (PLAN §3)** — launch `wtf-avatar-factory-pilot` (g6e.xlarge, DLAMI Ubuntu+NVIDIA, 300GB gp3, SSM-only, zero public ingress) · S3 `wtf-avatar-factory-246814138703` · IAM `wtf-avatar-factory-role` · quota request G-family 8→32 vCPU.
3. **Render box deploy (W3 RUNBOOK §2–4)** — sync fork → `bash wtf/render-kit/setup_l40s.sh` → `bash download_weights.sh` (copy `weights/download_manifest.txt` into `wtf/render-kit/evidence/`) → `python run_avatar.py --dry-run` → one smoke render → `python benchmark.py` → copy `bench_receipt.json` into evidence; fill RUNBOOK §4.3, re-run with locked budgets for PASS.
4. **Init DB** — `cd wtf/factory-core && python -m factory_core.cli init-db` (idempotent) → `python -m factory_core.cli doctor` to verify env + dark flags.
5. **Console** — `cd wtf/console && python -m uvicorn app:app --port 8795 --host 127.0.0.1`, reached via SSM port-forward; set `WTF_FACTORY_DB` / `WTF_MEDIA_ROOT` as needed.
6. **Content pass** — `cd wtf/content-brain && python calendar.py --days N --enqueue --db <factory.db>` → `factoryctl run` (drives queued→gated) → approve in console → `factoryctl publish` (dark stub).
7. **Capture (Phase 2)** — Vishal records per `wtf/capture-kit/SHOT_LIST.md`; then `python intake.py --dry-run` → real run → `assets/{refs,voice,face_clips}` + manifest.
8. **Publishing stays dark** until per-channel go: create `approvals/<channel>.json` (spec: `wtf/publishers/APPROVALS.md`) + `PUBLISH_LIVE=<channel>`; X additionally needs the multipart executor + chunk segmentation. LinkedIn: manual always.
9. **Box smoke** — `factoryctl demo` (offline 8/8), `qa-gate` selftest, `publisherctl gate-report`.
10. **Phase 4** — 3 test videos (Hindi/English/Hinglish) + blind panel vs HeyGen (PLAN §6) → GO/NO-GO. Rollback: `terminate-instances` (PLAN §8).

## 5. Self-audit

- Writes by synthesizer: this file + a byte-identical copy in the kanban workspace (`t_a9265b75/SYNTHESIS.md`); **0 files touched** in any W1–W7 package.
- Live calls: **0** — factory code is dark by default; synthesizer re-ran the suites with locally cached `pytest` via `uv` and `ffprobe 8.0.1`; no external API calls were made.
- Secrets printed or stored: **0**.
- Unverified claims: **0** — existence scans, self-audit scans, and the 7 fresh suite re-runs were executed for this rollup; remaining figures are cited from the verifier's report (`wtf/_evidence/VERIFICATION.md`).
- Deviation: **none.**
