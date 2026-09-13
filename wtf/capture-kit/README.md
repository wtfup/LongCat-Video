# README.md — W6 Capture Kit

Part of **WTF Avatar Factory v1** (`/Users/vishalnigammacminioffice/wtf-avatar-factory`,
contract row W6 in `BRIEF.md`, phase 2 in `PLAN.md`).

## 1. What this package is

The capture stage of the factory: everything needed to turn one recording session by Vishal into
validated factory assets.

| File | Role |
|---|---|
| `SHOT_LIST.md` | exact recording spec — camera/light/audio settings, 15 shots (3 outfits × 2 angles, Hindi + English monologues, 10-min casual talk, neutral clips, stills), talk-track beats, naming convention |
| `CAPTURE_CHECKLIST.md` | day-of run-sheet — gear, settings sheet, running order, offload + intake commands, warning policy |
| `voice_requirements.md` | voice-corpus spec — audio thresholds, coverage checklist, transcript-pairing format |
| `intake.py` | the tool — classifies, validates (ffprobe), organises raw footage into `assets/{refs,voice,face_clips}`, writes a manifest; `--dry-run` writes nothing |
| `tests/test_intake.py` | behavioral test suite (stdlib + pytest only) |

**Output layout** — `intake.py --dest <root>` writes exactly this, and nothing outside it:

```
<root>/assets/refs/          images + neutral clips      (identity reference / LoRA set)
<root>/assets/voice/         all audio files             (voice clone corpus)
<root>/assets/face_clips/    spoken video                (avatar fine-tune / lip-sync)
<root>/manifest.json         machine record of the run (schema `wtf.capture-kit.manifest/v1`)
```

`manifest.json` carries per-file `src`, `dst`, `rel`, `bucket`, `content`, `outfit`, `angle`,
`take`, `classified_by`, `sha256`, `bytes`, `probe` (ffprobe metadata), `checks` (validation
results), plus `skipped` / `ignored` / `errors` / `warnings` / `summary` for the whole run.
Recommended real-run dest: `/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data`
(shared factory data dir — the W1 queue DB lives beside it at `wtf/_data/factory.db`).

**Classification rule** (documented in full in `SHOT_LIST.md` §4):
`WTF_<yyyymmdd>_o<1|2|3>_<f|s>_<content>[_t<take>].<ext>` —
audio extensions always route to `voice/`; `still`/`neutral` route to `refs/`; the spoken tokens
(`hindi_mono`, `english_mono`, `casual`) route video to `face_clips/`; anything with a known media
extension but no content token is still ingested and flagged `content-token-missing`; unknown
extensions are skipped; OS junk files are recorded as ignored.

## 2. Quickstart

```bash
cd /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/capture-kit

# 1) offline self-test (no args, no files written outside a temp dir)
python3 intake.py --self-test

# 2) plan + validate a dump — writes NOTHING
python3 intake.py --source /path/to/RAW_DIR \
                  --dest   /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data \
                  --dry-run

# 3) real run (copies by default — camera originals untouched)
python3 intake.py --source /path/to/RAW_DIR \
                  --dest   /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data

# 4) test suite
python -m pytest -q
```

## 3. Reference

**CLI**

| Flag | Meaning |
|---|---|
| `--source DIR` | raw footage directory (required unless `--self-test`) |
| `--dest DIR` | assets root (required unless `--self-test`); output under `<dest>/assets/…` |
| `--dry-run` | plan + validate, write nothing, touch nothing |
| `--move` | move instead of copy (opt-in; source files are deleted **only** after a successful copy) |
| `--no-validate` | skip ffprobe validation (not recommended) |
| `--no-hash` | skip sha256 hashing (faster; disables content-based dedupe) |
| `--ffprobe PATH` | explicit ffprobe binary (default: PATH lookup) |
| `--json` | machine-readable result on stdout |
| `--self-test` | offline smoke test: classification table + dry-run purity |

**Exit codes:** `0` ok (warnings allowed) · `1` errors (zero-byte, unreadable/undecodable media,
copy failure — nothing invalid is copied) · `2` usage error or fail-closed gate (missing source,
dest inside source, ffprobe missing while validation is on — checked *before* any write).

**Validation rules** (all local ffprobe reads; warnings never fail a run):

| Check | Level | Trigger |
|---|---|---|
| `duration-out-of-range` | warn | clip outside the content window (neutral 8–30 s, monologues 60–240 s, casual 300–1800 s) |
| `resolution-below-1080p` / `resolution-not-4k` | warn / info | width < 1920 / < 3840 |
| `fps-below-24` | warn | video fps < 24 |
| `sample-rate-below-44k` | warn | audio < 44 100 Hz |
| `not-mono` | info | channels ≠ 1 |
| `content-token-missing` | warn | recognised media, unrecognised filename token (routed by extension) |
| `content-media-mismatch` | warn | token implies a different media kind than the file (audio exempt by design) |
| `duplicate-content` / `name-collision-renamed` | warn | re-run dedupe / same name, different bytes (`__dup2`…) |
| `missing-audio-stream` · `zero-bytes` · `probe-failed` | error | fails the file; run exits 1 |

## 4. Design decisions, safety, evidence

- **Local-only.** No network code: no `socket`/`urllib`/`http.client`/`requests`/`httpx` imports
  (pinned by a source-scan test). ffprobe/ffmpeg are local binaries.
- **Safe by default:** copy (never moves/deletes camera originals), dry-run purity, fail-closed
  before ffprobe-gated runs, deterministic ordering, idempotent re-runs (sha256 dedupe), atomic
  manifest write.
- **No secrets, no git, no AWS, no upstream edits** (per `BRIEF.md` hard rules).
- **Evidence map** (`evidence/`):

| File | Proof |
|---|---|
| `pytest-red.txt` | RED: suite failing before implementation (`ModuleNotFoundError`) |
| `pytest-run.txt` | GREEN: fresh full-suite run, all tests, real ffprobe/ffmpeg tests executed |
| `self-check.txt` | `intake.py --self-test` output |
| `dry-run-demo.txt` / `real-run-demo.txt` | end-to-end demo on synthetic media (real ffprobe validation, warnings + skip + ignore visible) |
| `demo/assets-root/` | the demo's actual output tree + `manifest.json` |
| `secret-scan.txt` | credential-pattern scan over all package files |
| `file_inventory.txt` | full file list + sha256 hashes |

## 5. Self-audit

- Authored files in this package: **8** (6 package files + 2 test files; hashes in
  `evidence/file_inventory.txt`). Generated evidence files: **18**.
- Live calls: **0** — no network imports exist in `intake.py` (enforced by a test); all validation
  is local ffprobe; `--self-test`, dry-run and tests all run offline.
- Secrets printed or stored: **0** (scan: `evidence/secret-scan.txt`).
- Unverified claims: **0** — every behavior claimed in this README is exercised by
  `evidence/pytest-run.txt`; demo numbers come from `evidence/real-run-demo.txt` and its manifest.
- Deviations: none.
