# W3 — Render Kit: Models

## 1. Inventory (what the factory uses)

| Role | Model | Where it comes from | Why |
|---|---|---|---|
| Avatar spine | **LongCat-Video-Avatar-1.5** | HF `meituan-longcat/LongCat-Video-Avatar-1.5` | Whisper-large-v3 audio encoder, distillation (8-step) — the model PLAN.md pins for the pilot |
| Foundation | **LongCat-Video** | HF `meituan-longcat/LongCat-Video` | supplies `tokenizer/`, `text_encoder/` (UMT5), `vae/` (Wan) that the avatar pipeline loads from `checkpoint_dir/../LongCat-Video` |
| Distill LoRA | `dmd_lora.safetensors` | inside the avatar repo (`lora/`) | required for `--use_distill` on v1.5 |
| Audio encoder | `whisper-large-v3` | inside the avatar repo (`whisper-large-v3/`) | v1.5 lip-sync encoder |
| Vocal separator | `Kim_Vocal_2.onnx` | inside the avatar repo (`vocal_separator/`) | separates vocals before audio embedding |
| INT8 DiT | `base_model_int8/` | inside the avatar repo | `--use_int8` reduced-VRAM DiT |

`LongCat-Video-Avatar` **v1.0** (wav2vec2 variant) is deliberately *not*
downloaded: the factory profile is v1.5-only, and the README states `--use_int8`
is v1.5-only, `--use_distill` is required for v1.5.

## 2. Exact HF ids and on-disk layout (all under `<repo>/weights/`)

Downloaded by `download_weights.sh` (ids taken verbatim from the WTF fork
`README.md` "Model Download", lines 105–107):

```text
weights/
├── LongCat-Video/                              # meituan-longcat/LongCat-Video
│   ├── tokenizer/        ← run_demo…py:113  (AutoTokenizer)
│   ├── text_encoder/     ← run_demo…py:114  (UMT5EncoderModel)
│   └── vae/              ← run_demo…py:115  (AutoencoderKLWan)
└── LongCat-Video-Avatar-1.5/                   # meituan-longcat/LongCat-Video-Avatar-1.5
    ├── base_model/                            (bf16 DiT — not used by the int8 profile)
    ├── base_model_int8/  ← run_demo…py:128 / quantization.py:158  (--use_int8)
    │   ├── config.json
    │   └── quantized_model.safetensors.index.json  (or *.safetensors single-file fallback,
    │                                               quantization.py:208–225)
    ├── lora/dmd_lora.safetensors ← run_demo…py:132  (--use_distill)
    ├── scheduler/         ← run_demo…py:119  (FlowMatchEulerDiscreteScheduler, v1.5)
    ├── whisper-large-v3/  ← run_demo…py:143  (v1.5 audio encoder)
    └── vocal_separator/Kim_Vocal_2.onnx ← run_demo…py:147
```

Reference: `run_demo_avatar_single_audio_to_video.py` at the WTF fork root.
The **directory names are load-bearing**: the upstream code resolves the
foundation model as `os.path.join(checkpoint_dir, '..', 'LongCat-Video')`.

## 3. Quantization, distillation, and sampling constants

- `--use_int8` (v1.5 only): loads the quantized DiT via
  `load_quantized_dit()` — weight-only INT8, per-channel symmetric, with
  `final_layer.linear` skipped (`quantization.py:41–43, 158–171`). The state
  dict is loaded `strict=True` (`quantization.py:227`), so an incomplete
  `base_model_int8/` hard-fails rather than silently dequantizing garbage.
- `--use_distill` (required for v1.5): loads `lora/dmd_lora.safetensors`
  (`lora_network_dim=128, alpha=64`, multiplier 1.0) and **forces**
  `num_inference_steps=8`, `text_guidance_scale=1.0`,
  `audio_guidance_scale=1.0` (`run_demo…py:71–74, 131–135`).
- Sampling constants used for all expected-output arithmetic
  (`run_demo…py:77–83`):

  | constant | v1.5 value |
  |---|---|
  | `save_fps` | 25 |
  | `audio_stride` | 1 |
  | `num_frames` (first segment) | 93 |
  | `num_cond_frames` (per continuation) | 13 |

  Hence `expected_output_seconds(n) = (93 + (n−1)·80) / 25` →
  n=1: 3.72 s, n=3: **10.12 s** (the benchmark's ~10 s target).

- Single-GPU discipline: the pilot box has **one** L40S, so the kit always
  passes `--context_parallel_size=1` with `--nproc_per_node=1`. Upstream README
  examples use cp=2 for 2-GPU machines — do not copy those here.
- 720p exists (`--resolution 720p`) but is **unmeasured** on the pilot box;
  default stays 480p until `benchmark.py` produces a receipt for 720p.

## 4. Provenance & integrity rules

1. **Revision pinning:** `download_weights.sh --revision <ref>` pins the HF
   revision; record the ref in the manifest when a non-`main` revision is used.
2. **Manifest:** after every download, the script writes
   `weights/download_manifest.txt` with per-tree `du -sh`, sha256 of all
   config/index/onnx files, and (with `--hash-all`) sha256 of every weight
   file. **No sizes or hashes are asserted in this document** — the weights
   have not been downloaded in this environment; the manifest records the
   real values on the box.
3. **Fail closed:** `download_weights.sh --verify-only` and
   `run_avatar.py`'s preflight both check the same required paths and refuse to
   proceed when any is missing (`evidence/` shows both the refusing and the
   accepting runs).
4. **Licence:** weights are MIT-licensed (WTF fork `README.md:261`); the fork
   itself carries the WTF licence file at the repo root.

## 5. Self-audit

- **Writes:** 14 tracked files authored in this package (3 docs, 3 Python
  modules, 3 shell scripts, `requirements.txt`, 4 test files); this doc is one
  of the 3 main docs, all ending with this same audit block.
- **Live calls during authoring/verification: 0.** No model weights were
  downloaded; no HF/AWS/API request was made.
- **Secrets printed or stored: 0.** Both HF repos are public, MIT-licensed.
- **Unverified claims: 0.** File sizes, VRAM fit, and throughput are
  explicitly *not* claimed — they are deferred to `download_manifest.txt`
  (sizes/hashes) and `bench_receipt.json` (VRAM + timing) produced on the box.
- **Deviation:** none.
