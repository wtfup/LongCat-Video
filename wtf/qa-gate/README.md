# WTF QA Gate v1 — `wtf/qa-gate`

Quality/realism gate for Avatar Factory renders. Sits between the renderer (best-of-2)
and the Console approval step:

```
render (best-of-2)  ->  [ QA GATE ]  ->  Console approval  ->  publish queue
```

**Dark by default.** The gate runs only local `ffprobe`/`ffmpeg` subprocesses — zero
network, zero AWS, zero git. The two model-based metrics (lip-sync WER, face similarity)
are stubbed IFACEs in v1: they report `unavailable` and are excluded from the score —
values are **never fabricated**. Live scorers plug in on the render box behind the same
interface (see §3).

---

## 1. Scoring model & policy

Policy file: [`policy.yaml`](policy.yaml) — thresholds, weights, detector parameters.
Editing thresholds is a policy change; the gate records the policy `sha256` in every
report so results are traceable to the exact policy used. PyYAML is used when
installed; without it the gate falls back to a built-in parser for the documented
policy subset (block/flow maps + scalar values) — every report records which parser
ran (`policy_parser`).

| Check | Measures | Required | Weight | Notes |
|---|---|---|---|---|
| `integrity` | ffprobe parse, video+audio streams, duration bounds, expected-duration fit, full decode-error scan | yes | 0 (hard gate) | any failure ⇒ reject |
| `black_frames` | `blackdetect` ratio vs `max_black_ratio` | yes | 1.0 | sub-score `1 − ratio/max`, clamped |
| `silence` | `silencedetect` ratio vs `max_silence_ratio` | yes | 1.0 | sub-score `1 − ratio/max`, clamped |
| `loudness` | `volumedetect` mean dBFS vs floor | no | 0.5 | dead-audio detection |
| `lip_sync_wer` | WER of rendered speech vs script (ASR) | no | 3.0 | **stub IFACE in v1** |
| `face_sim` | identity similarity vs reference faces | no | 3.0 | **stub IFACE in v1** |

**Check status vocabulary**

- `pass` — measured and within policy
- `fail` — measured and outside policy
- `skipped` — **not applicable** (e.g. audio checks on an input whose audio requirement
  was waived by policy, or downstream checks when the probe itself failed)
- `unavailable` — metric could not be produced (stub IFACE, or a *required* metric that
  is missing ⇒ reject with `required_metric_unavailable`)
- `error` — a check crashed; treated as a failure when required

**Decision logic** (fail closed):

1. any required check `fail`/`error` ⇒ **reject**
2. any required check `unavailable` ⇒ **reject** (`required_metric_unavailable`)
3. no scorable checks ⇒ **reject** (`no_scorable_checks`)
4. `score < accept_score` ⇒ **reject** (`below_accept_score`)
5. otherwise ⇒ **accept**

`score = 100 × Σ(weight·sub-score) / Σ(available weights)`. Weights of unavailable
checks are renormalized out (`score_basis` in the report shows exactly which checks
were scored and the weight math).

**Reason codes** (machine-readable, in every report): `file_not_found`, `probe_failed`,
`no_video_stream`, `no_audio_stream`, `duration_unknown`, `duration_below_min`,
`duration_above_max`, `duration_mismatch`, `decode_errors`, `black_ratio_exceeded`,
`silence_ratio_exceeded`, `loudness_below_floor`, `below_accept_score`,
`required_metric_unavailable`, `no_scorable_checks`, `<check>_error`.

## 2. Usage

### CLI (`gate.py`)

Exit codes: `0` accept · `2` reject · `1` error.

```bash
cd /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/qa-gate

python gate.py selftest                                   # tool/policy availability
python gate.py score  --video render.mp4 [--expect-duration 45] [--json report.json]
python gate.py bestof --candidates a.mp4,b.mp4 [--json selection.json]
python gate.py record --db factory.db --job <id> --report report.json [--init-db]
python gate.py run    --db factory.db --job <id> [--expect-duration 45] [--init-db]
```

`run` is the pipeline integration point: it reads `render_path` from the job, evaluates
it, writes the result back, and exits 0/2 by verdict.

### Python API

```python
import gate

report = gate.evaluate("/abs/render.mp4", expect_duration=45.0)   # -> dict (JSON-safe)
sel    = gate.select_best_of_n(["/abs/a.mp4", "/abs/b.mp4"])      # winner + per-candidate
gate.record_result(db_path, job_id, report)                       # writes gate_score/gate_json
gate.gate_job(db_path, job_id)                                    # evaluate + record in one call
```

**Best-of-N**: scores every candidate; the winner is the highest-scoring
**eligible** (verdict `accept`) candidate, ties broken by list order. If no candidate
is eligible the selector fails closed (`winner: null`, reason `no_eligible_candidate`).

## 3. Factory DB integration (schema v1)

The gate reads/writes the shared factory DB (`wtf/_data/factory.db` on the box;
`--db` overrides). Writes on `record_result` / `gate_job`:

| Verdict | `jobs.status` | `jobs.gate_score` / `gate_json` | `events.event` |
|---|---|---|---|
| accept | `gated` | score + full report JSON | `gate_passed` |
| reject | `rejected` | score + full report JSON | `gate_rejected` |

Recording is allowed only from states `rendered` / `gated` / `rejected` (re-gate).
Anything else (`queued`, `rendering`, `approved`, `published`) ⇒ `DbError`
`invalid_state` — fail closed. Unknown job ⇒ `unknown_job`. Accept verdicts without a
numeric score ⇒ rejected at write time. `--init-db` bootstraps the canonical schema
(idempotent, `CREATE TABLE IF NOT EXISTS`) for standalone use; W1 owns schema creation
in the normal pipeline.

### The `gate_json` report contract

`gate_id`, `gate_version`, `policy_id`, `policy_sha256`, `policy_parser`, `video`
(absolute), `video_sha256`, `duration_s`, `expected_duration_s`, `evaluated_at`,
`probe`, `checks[]` (id/status/required/weight/score/value/detail/codes), `score`,
`score_basis` (available_weight/total_weight/scored), `verdict`, `codes[]`, `reasons[]`.

### v1.5 live-scorer hooks (deliberately not executed in v1)

- `LipSyncWERScorer(mode="live")` and `FaceSimScorer(mode="live")` raise
  `LiveScoringDisabled` while the factory runs dark. The `word_error_rate()` math
  (Unicode-aware, Hindi-safe) is implemented and tested so the live scorer only needs
  the ASR/embedding halves.
- Policy `checks.<id>.required: true` flips a metric into hard-gate behavior.

## 4. Tests & evidence

```bash
cd /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/qa-gate
python -m pytest            # 72 tests, ~7s (synthetic fixtures generated with ffmpeg)
bash evidence/reproduce.sh  # regenerates the entire evidence pack
```

The suite runs on **stdlib + pytest alone** — proven under four invocations:
`python -m pytest` (venv, PyYAML present); `uv run --no-project --with pytest
python -m pytest` (bare env → built-in policy parser); the same with `--with pyyaml`;
and `--python /usr/bin/python3` (3.9 floor). PyYAML-equivalence of the built-in parser
is asserted by `test_builtin_parser_matches_active_parser`. Both logs:
`evidence/pytest_fresh.log`, `evidence/pytest_fresh_uv_bare.log`.

Coverage: policy fail-closed loading (both parsers) · integrity (corrupt/missing/
no-audio/short/duration-mismatch/decode) · black/silence/loudness detectors on
synthetic good/bad clips · scoring math + threshold boundary · stub IFACEs + WER math
(EN + Hindi) · best-of-N selection + ties + ineligible candidates · DB
record/reject/fail-closed states · CLI contract (exit codes, JSON) · adversarial
inputs (injection-shaped filenames, forged reports, hostile YAML).

Evidence pack: `evidence/` — start at `evidence/EVIDENCE.md` (index + results) and
`evidence/pytest_fresh.log` (fresh full-suite run).

## 5. Self-audit

- **Writes**: 34 deliverable/evidence files, all inside `wtf/qa-gate/` (plus tool-managed pytest/bytecode caches). 0 writes outside the package; the shared factory DB and upstream repo files are untouched.
- **Live calls**: 0. `gate.py` + `tests/` contain no network primitives (socket/urllib/requests/httpx/boto3/http(s) URLs — scan: `evidence/selfaudit_scan.txt`, 0 hits). The only subprocesses are local `ffprobe`/`ffmpeg` (pinned by `selftest`).
- **Secrets printed**: 0 — secret-pattern scan over deliverables + logs: 0 hits (`evidence/selfaudit_scan.txt`). No credentials exist in this package.
- **Unverified claims**: 0 — every claim above is backed by a re-runnable artifact: 72/72 tests green in four fresh invocations incl. the stdlib-only bare env and the 3.9 floor (`evidence/pytest_fresh.log`, `evidence/pytest_fresh_uv_bare.log`, `evidence/environment.txt`), CLI verdicts (`evidence/cli_demo.txt`, `evidence/report_*.json`), DB writes (`evidence/db_demo.txt`), one-command reproduction (`evidence/reproduce.sh`). Exact deviation: the two model-based metrics are intentionally stubbed (`unavailable`) per the v1 dark contract — they are excluded from scoring and never fabricated, as shown in every report's `checks[]` and `score_basis.scored`.
