# W3 — Render Kit

Deploy + benchmark kit for rendering **WTF Avatar Factory** clips on the pilot
box (`wtf-avatar-factory-pilot`: g6e.xlarge, 1x L40S 48GB, DLAMI Ubuntu, SSM-only).
Everything here targets the locked profile:

```
LongCat-Video-Avatar 1.5 · avatar-v1.5 · --use_int8 · --use_distill · context_parallel_size=1
```

## 1. Deliverables (exact paths)

| File | Purpose |
|---|---|
| `setup_l40s.sh` | Idempotent box provisioning (apt, venv, torch 2.6.0+cu124, flash-attn, upstream reqs) |
| `download_weights.sh` | Exact-HF-id weight download + layout verification + manifest |
| `run_avatar.py` | Stdlib-only launcher; preflight-validates the checkpoint layout, builds the exact upstream `torchrun` command; refuses to run against a partial layout |
| `benchmark.py` | Measured throughput benchmark → `bench_receipt.json`; `--self-test` is fully offline |
| `models.md` | Model inventory, on-disk layout, provenance rules |
| `RUNBOOK.md` | Deploy → weights → render → benchmark operations runbook |
| `self_audit.py` | Secret scan + audit JSON (used by tests and evidence) |
| `make_evidence.sh` | Regenerates `evidence/` offline |
| `tests/` | pytest suite (stdlib-only, no GPU, no network) |

## 2. Quick start (on the pilot box)

```bash
bash setup_l40s.sh                       # provision (idempotent; --check-only to verify)
bash download_weights.sh                 # exact HF ids -> $REPO/weights
python run_avatar.py --dry-run           # offline plan + preflight check
python run_avatar.py --input-json assets/avatar/single_example_1.json \
    --output-dir ./outputs_avatar_single --stage-1 ai2v --num-segments 1
python benchmark.py --budget-sec-per-10s <LOCKED>   # writes bench_receipt.json
```

This machine (the Mac) has **no GPUs and no weights**; the two Python tools
were exercised here strictly via `--dry-run` / `--self-test`, and both shell
scripts only via `bash -n`. That is intentional: the BRIEF forbids live calls
from workers.

## 3. Verification

```bash
PYTHON=/path/to/python bash make_evidence.sh    # regenerates evidence/
python -m pytest -q                             # full suite, from this directory
python self_audit.py --json                     # secret scan + deliverable audit
```

Evidence produced by the last run lives in `evidence/` — see
`evidence/README.txt` for the artifact index.

## 4. Failure model

- `run_avatar.py` exits **2** and lists exact missing paths when the checkpoint
  layout is incomplete; it never starts torchrun against a partial layout.
- `benchmark.py` measured mode exits **2** (no receipt) when preflight fails;
  exits **1** when a receipt was written but a gate FAILED.
- `benchmark.py --self-test` refuses to overwrite a measured receipt unless
  `--force` is passed.

## 5. Self-audit

- **Writes:** 14 tracked files authored in this package — 3 docs (`README.md`,
  `models.md`, `RUNBOOK.md`), 3 Python modules (`run_avatar.py`, `benchmark.py`,
  `self_audit.py`), 3 shell scripts (`setup_l40s.sh`, `download_weights.sh`,
  `make_evidence.sh`), `requirements.txt`, and 4 test files under `tests/`.
  Nothing was written outside `wtf/render-kit/` (evidence + scratch fixtures live
  inside `evidence/`).
- **Live calls while building/verifying: 0.** `setup_l40s.sh` /
  `download_weights.sh` perform apt/pip/HF traffic *only when an operator runs
  them on the pilot box*; here they were verified with `bash -n` and
  `download_weights.sh --verify-only` (offline). `run_avatar.py` was exercised
  with `--dry-run`; `benchmark.py` with `--self-test` and stubbed subprocesses
  in tests.
- **Secrets printed or stored: 0.** `self_audit.py` credential scan over the
  package: 0 findings (see `evidence/self_audit.json`). No tokens, keys, or
  account IDs are handled by any file here.
- **Unverified claims: 0.** Every upstream path/flag/constant is cited to a
  line in the WTF fork (`run_demo_avatar_single_audio_to_video.py`,
  `README.md`, `longcat_video/modules/quantization.py`). All hardware numbers
  (sec-per-10s, VRAM, throughput) are explicitly *unmeasured templates* that
  `benchmark.py` fills in on the box; no synthetic number is presented as real.
- **Deviation:** none.
