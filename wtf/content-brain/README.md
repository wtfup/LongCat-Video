# W4 — Content Brain (WTF Avatar Factory v1)

Turns the curated WTF content library into **daily production briefs** for the
avatar factory: hook, 45-second script in **Hindi + English**, b-roll shot
list, caption + hashtags, thumbnail idea — plus a deterministic publishing
**calendar** that can enqueue render jobs into the shared factory database.

Everything here is **dark by default**: the LLM interface is stubbed, output
is deterministic (no randomness), there are **zero network calls**, and any
live path refuses unless explicitly enabled.

## 1. Files

| File | What it is |
|---|---|
| `brain.py` | Brief engine + CLI (`python brain.py`). Also the YAML loader (PyYAML, with a stdlib-only fallback parser for `pillars.yaml`). |
| `pillars.yaml` | Curated library: 5 pillars × 2 topics (bilingual), hook/context/CTA template banks, hashtags, thumbnail concepts, brands, rotation. |
| `calendar.py` | Planner + CLI (`python calendar.py`) + factory.db enqueue (shared schema v1). |
| `providers.py` | LLM interface — STUBBED. `StubProvider` (deterministic, offline) is the only constructible default; `LiveProvider` refuses (v1 dark) unless `CONTENT_BRAIN_LIVE=1` and even then is not wired. |
| `prompts/` | 7 prompt templates (system, daily brief, per-language script, shot list, caption+hashtags, thumbnail). Rendered into `prompt_audit` on every brief. |
| `samples/` | The 10 sample briefs (2 per pillar) + `index.json` (sha256 per file). |
| `tests/` | 70 tests — run with `python -m pytest tests -q`. Sockets are hard-disabled for the whole suite (proves 0 live calls). |
| `self_audit.py` | Package auditor: required files, network imports, secret scan, manifest (sha256). Run `python self_audit.py --write`. |
| `requirements.txt` | Pinned deps (PyYAML + pytest; runtime is stdlib-only). |

## 2. Interfaces

**Brief ID** `cb-<YYYY-MM-DD>-<pillar>-<NN>` (e.g. `cb-2026-09-14-ai_systems-01`).
Format `9:16_vertical_reel`, `45s`, languages `["en", "hi"]`. Beat sheet:
`0:00–0:03 HOOK · 0:03–0:11 CONTEXT · 0:11–0:19 POINT 1 · 0:19–0:27 POINT 2 ·
0:27–0:35 POINT 3 · 0:35–0:40 PROOF · 0:40–0:45 CTA`.

**Calendar plan JSON** — `{"generated_by", "start", "days", "per_day", "slots": [...]}`
with per-slot `date/weekday/pillar/topic_id/brief_id/brand/slot_index/languages/format/duration_s`.

**factory.db enqueue (shared schema v1 — owned by W1)** — `enqueue_plan()` writes
**one `queued` job per language** per slot, ids `<brief_id>-<lang>` (idempotent
`INSERT OR REPLACE`; re-running does not duplicate). The writer verifies the
existing `jobs` table against schema v1 and fails closed on mismatch. Events
are W1's domain — this package writes jobs only.

## 3. Usage

```bash
# Daily brief (markdown to stdout; IST by default)
python brain.py --date 2026-09-14 --pillar ai_systems --topic ai_systems-01

# Machine-readable brief
python brain.py --date 2026-09-14 --json

# Regenerate the 10 samples + index.json
python brain.py --write-samples

# 7-day plan (weekday rotation), JSON, or straight to a file
python calendar.py --days 7 --start 2026-09-14
python calendar.py --days 7 --start 2026-09-14 --out calendar.json

# Opt-in: enqueue render jobs into the factory DB (writes nothing without --enqueue)
python calendar.py --days 7 --enqueue --db /absolute/path/factory.db

# Tests + audit
python -m pytest tests -q
python self_audit.py --write
```

## 4. Determinism & safety notes

- Same inputs → byte-identical briefs, plans and samples (tests enforce this).
- `prompt_audit` on each brief records kind + template + sha256 of the exact
  prompt that a live model would have received.
- The stub is the only reachable provider; live mode requires
  `CONTENT_BRAIN_LIVE=1` and still raises `LiveNotWired`. No HTTP client is
  imported anywhere in runtime code (audited by `self_audit.py`).
- Word budget band (tests): 60–160 VO words per language (measured library:
  68–101) — scripts hold VO plus deliberate b-roll/text-card pauses inside
  the 45s cut.

## 5. Self-audit

- **Writes:** 40 files in this package (see `evidence/manifest.json` for sha256 per file).
- **Live calls:** 0 — no network imports in runtime code; the entire test suite runs with sockets hard-disabled; every provider path is deterministic/offline.
- **Secrets printed:** 0 — `self_audit.py` secret-scans every text artifact (deliverables + evidence); findings: none.
- **Unverified claims:** 0 — every claim in this README is backed by an executed command or test (evidence in `evidence/`); publishing stays dark, LinkedIn manual, and no spend/outbound happens in this package.
- **Deviation:** none.

Audit proof: `python self_audit.py --write` → status PASS
(`evidence/self_audit.txt`, `evidence/manifest.json`). Test proof:
`evidence/pytest_run.log` (fresh full-suite execution).
