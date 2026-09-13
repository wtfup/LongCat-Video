# W3 — Render Kit: RUNBOOK

Operational procedure for the avatar render box. Written against the pilot
box defined in `PLAN.md` §3 (g6e.xlarge, 1x L40S 48GB, DLAMI Ubuntu + NVIDIA,
300GB gp3, SSM-only access, zero public ingress) and the locked profile from
`models.md` (int8 + distill + cp=1). Box provisioning and instance lifecycle
are the **parent's** job (AWS); this runbook starts once you have an SSM shell.

## 1. Prerequisites

- SSM session from the parent (task-owner) side, e.g.
  `aws ssm start-session --target <instance-id>` — run by the parent, not by
  this package.
- The WTF fork synced to the box (e.g. `~/wtf-avatar-factory`) — the parent
  handles repo sync/commits. There is **no git step in this kit**.
- ~an hour of wall-clock for provisioning (flash-attn compiles from source).

## 2. Phase A — provision the box

```bash
cd ~/wtf-avatar-factory/wtf/render-kit
bash setup_l40s.sh                 # full provisioning (idempotent)
bash setup_l40s.sh --check-only    # later: verify torch + CUDA only
```

What it does: verifies L40S + python3.10, apt-installs `python3.10-venv`,
`ffmpeg`, `libsndfile1`, build tools; creates `~/.venvs/wtf-avatar`; installs
the exact upstream pins — `torch==2.6.0+cu124` trio (README:74),
`flash_attn==2.7.4.post1` (README:76–80), then `requirements.txt` and
`requirements_avatar.txt`; verifies `torch.cuda.is_available()`; writes
`~/.wtf-render-kit/setup_l40s.receipt.txt`; prints next steps.

Flags: `--skip-apt` (pre-built image), `--skip-flash-attn` (debug only),
`--repo-dir`, `--venv-dir`, `--receipt-dir`.

**Expected end state:** the verify block prints module versions and
`cuda device: NVIDIA L40S`. If flash-attn fails to build, capture the compiler
error; the model config enables FlashAttention-2 by default, so either fix the
toolchain and re-run or install xformers and switch the attention backend in
the model config.

## 3. Phase B — weights

```bash
cd ~/wtf-avatar-factory/wtf/render-kit
bash download_weights.sh                # exact HF ids (see models.md §2)
bash download_weights.sh --verify-only  # re-check layout at any time (offline)
```

Downloads `meituan-longcat/LongCat-Video` → `weights/LongCat-Video` and
`meituan-longcat/LongCat-Video-Avatar-1.5` → `weights/LongCat-Video-Avatar-1.5`
(ids from the fork README:105–107). Options: `--revision <ref>` to pin,
`--fast` (hf_transfer), `--hash-all` (full sha256 manifest — slow on ~100 GB).

On success it writes `weights/download_manifest.txt` (sizes + hashes) — copy
that into `evidence/` when reporting the deploy. If verification fails, the
script lists every missing path and **exits 1 without deleting anything**;
reconcile the HF repo layout before rendering. `run_avatar.py` independently
enforces the same layout, so a partial download cannot start a render.

**Where the manifest and receipts belong:** the render box's `weights/` is not
committed; the parent copies `download_manifest.txt` / `bench_receipt.json`
into `wtf/render-kit/evidence/` when recording the deploy.

## 4. Phase C — render & benchmark

### 4.1 One render (smoke)

```bash
cd ~/wtf-avatar-factory/wtf/render-kit
python run_avatar.py --dry-run                       # preflight + exact command, runs nothing
python run_avatar.py \
  --input-json assets/avatar/single_example_1.json \
  --output-dir ./outputs_avatar_single --stage-1 ai2v --num-segments 1
```

`run_avatar.py` builds the locked command
(`--model_type avatar-v1.5 --use_int8 --use_distill --context_parallel_size 1`,
launched via `torchrun --standalone --nproc_per_node=1` from the repo root) and
streams output. Exit codes: `0` ok, `2` preflight refusal, otherwise the
upstream exit code. The final assembled video is
`<output-dir>/video_continue_<n>.mp4` for `n>1` segments, else
`<output-dir>/<stage>_demo_1.mp4`.

To render from a queue job, materialize the upstream input JSON first:

```bash
python run_avatar.py --make-input-json /tmp/job_123.json \
  --prompt "<script>" --audio /assets/voice/job_123.wav --image /assets/refs/vishal.png
```

### 4.2 Benchmark (the Phase-1 numbers)

```bash
python benchmark.py --dry-run            # show the exact commands; runs nothing
python benchmark.py                      # measured, default 3 segments (~10.12s output)
python benchmark.py --repeats 3 --budget-sec-per-10s <LOCKED> --budget-peak-vram-gb 46
```

Measured mode writes `bench_receipt.json` with, per repeat: wall seconds,
exit code, peak VRAM (sampled every 2 s via `nvidia-smi`), output file +
ffprobe facts, and the per-repeat log tail. Derived metrics:
`sec_per_10s_render`, `renders_per_gpu_hour`, `gpu_hours_per_content_hour`
(formulas in `benchmark.py` header). Gates: `runs_exit_zero`,
`outputs_present`, `peak_vram_within_budget`, `sec_per_10s_within_budget` —
each `PASS | FAIL | BASELINE_PENDING`. Exit codes: `0` all green, `1` receipt
written but a gate FAILED, `2` blocked before running (no receipt).

`--self-test` is fully offline (no GPU/network/weights): it writes a receipt
marked `"synthetic": true` with fixture numbers and refuses to overwrite a
measured receipt unless `--force`. Never quote a synthetic receipt as a real
number.

### 4.3 Throughput table — TO BE LOCKED from the first measured run

Fill this in from `bench_receipt.json` (`metrics` + `host`), then set the
budgets to those values and re-run so the gates return PASS. Until then these
cells are intentionally empty — this kit ships **no invented numbers**.

| Metric | Value | Receipt field |
|---|---|---|
| GPU | _e.g. L40S 48GB (from host)_ | `host.gpu_name` |
| Peak VRAM (GB) | **_fill_** | `metrics.peak_vram_gb` |
| sec per 10 s render | **_fill_** | `metrics.sec_per_10s_render` |
| Renders / GPU-hour (10 s each) | **_fill_** | `metrics.renders_per_gpu_hour` |
| GPU-hours per content-hour | **_fill_** | `metrics.gpu_hours_per_content_hour` |

### 4.4 Failure playbook

| Symptom | First action |
|---|---|
| `run_avatar.py` exits 2, lists missing paths | Run `download_weights.sh`, then `--verify-only`; reconcile HF layout before retrying |
| `torch.cuda.is_available()` false | `nvidia-smi` + re-run `setup_l40s.sh --check-only`; wrong venv or driver |
| flash-attn build error | Capture compiler error; fix toolchain and re-run, or use xformers backend (see §2) |
| CUDA OOM mid-render | Confirm the plan shows `--use_int8` (if not, you are not on the locked profile); do not stack renders; re-measure VRAM |
| ffmpeg error during save | `sudo apt-get install -y ffmpeg`, re-run |
| benchmark repeat times out | Inspect `measurements[].log_tail`; raise `--timeout-s` only after reading the log |
| `vocal separator` assert "No vocal detected" | Input audio problem (music/no speech); the separator model path is fine if preflight passed |

Housekeeping: the upstream demo creates `./audio_temp_file/` in the repo root
and removes its temp vocals; `outputs_*` and `weights/` are artifacts, not
source — keep them out of commits. Rollback of the box itself is the parent's
`terminate-instances` step (PLAN §8).

## 5. Self-audit

- **Writes:** 14 tracked files authored in this package (3 docs, 3 Python
  modules, 3 shell scripts, `requirements.txt`, 4 test files). This runbook
  describes operations only; nothing outside `wtf/render-kit/` was written.
- **Live calls during authoring/verification: 0.** The commands above are
  *documentation for the box*; on this Mac, `run_avatar.py`/`benchmark.py` ran
  only in `--dry-run`/`--self-test`, and both shell scripts ran only under
  `bash -n` / `--verify-only` (offline).
- **Secrets printed or stored: 0.** No credentials appear in this runbook;
  SSM handles access at the transport layer, and the only referenced IDs are
  public HF model ids.
- **Unverified claims: 0.** Box specs are attributed to `PLAN.md` §3; upstream
  flags/paths to the fork source; the throughput table is explicitly a
  to-be-locked template rather than filled with invented numbers.
- **Deviation:** none.
