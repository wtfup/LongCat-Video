# SHOT_LIST.md — W6 Capture Kit

**Owner of the session:** Vishal Nigam (one-time recording) · **Consumer:** WTF Avatar Factory
(`wtf/content-brain` scripts → voice clone → LongCat-Video-Avatar 1.5 render on L40S → W7 QA gate)
**Repo:** `/Users/vishalnigammacminioffice/wtf-avatar-factory` · **Package:** `wtf/capture-kit/`

## 1. What this session delivers

One recording session (budget **≈ 60 minutes including setup**) produces the three raw asset
classes the factory needs, organised by `intake.py` into:

| Destination | Contents | Used for |
|---|---|---|
| `assets/refs/` | 9 high-res stills (3 per outfit) + **6× 15 s neutral clips** (3 outfits × 2 angles) | identity reference / LoRA training set |
| `assets/voice/` | Hindi 2-min monologue WAV · English 2-min monologue WAV · 10-min casual talk WAV · 30 s room tone | voice clone corpus |
| `assets/face_clips/` | the two monologues ×2 angles + the casual talk, as video (talking-head performance) | avatar fine-tune / lip-sync reference |

This is a **specification, not a suggestion**: shots below are the exact set intake validates
against (expected durations are the live constants in `intake.py`, exercised by the tests).

## 2. Camera, lighting, sound — exact spec

### 2.1 Camera

- **Resolution — capture 4K (3840×2160)** for every shot. Absolute floor **1080p (1920×1080)**;
  do not mix resolutions mid-session. (intake warns below 1920px wide and notes anything < 4K.)
- **Frame rate: 25 or 30 fps**. Shutter **1/50** (at 25fps) or **1/60** (at 30fps) — 180° rule.
  Do **not** use 60/120fps modes.
- **Aperture f/2.8–f/4** (chest-up framing, background softly out of focus, eyes sharp).
- **ISO: lowest that exposes correctly** (≤ 1600). **White balance: manual 5600K** — never AWB.
- **Focus: manual, locked on the eyes** after first setup; autofocus OFF. No digital zoom.
- **No beauty / skin-smoothing / HDR modes. No filters.**
- **Codec/bitrate:** H.264/H.265 at **≥ 100 Mbps** at 4K (or ProRes if available); MP4 or MOV.
- **Camera position:** eye level, **1.2–1.5 m** from the face, on a tripod. Frame = **chest-up
  medium close-up**, eyes on the upper-third line, 5–10 % headroom. Camera does **not** move
  between takes of the same shot.

### 2.2 Lighting

- **Soft key light at 45° camera-left, ~45° above eye line**, as close as practical.
- **Fill or reflector camera-right**, face:shadow ratio ≈ **2:1**. No hard shadows on the face.
- Background **1–1.5 stops darker** than the face. No window behind talent (no backlight/blowout).
- **One colour temperature only (5600K).** No mixed indoor/outdoor light. No ceiling-only lighting.
- The lighting setup is kept identical across all outfits so training frames stay consistent.

### 2.3 Sound

- **Lavalier mic** clipped 15–20 cm below the chin, cable taped to the shirt; DJI Mic / Rode
  Wireless-class kit or equivalent. Phone-mic audio is not acceptable except as an emergency backup.
- Record **48 kHz** (24-bit preferred). Monitor levels: average **−18 to −12 dBFS**, peaks **≤ −6 dBFS**.
- **Spoken shots get a WAV companion** (same take, same stem, `.wav`) recorded by the lav's own
  recorder — this is the voice-clone corpus; the camera take is for face/performance. Minimum
  required companions: **S04 (hindi, front), S09 (english, front), S14 (casual)** + room tone.
- **Room tone: 30 s of silence at the start** (stand still, don't speak) — see S01.
- Room: closed, soft furnishings, no fan/AC hum during takes, phones on silent, no music.

### 2.4 Wardrobe & grooming (3 outfits)

| Outfit | Wear | Rules |
|---|---|---|
| **O1** | WTF-branded black tee | solid, matte; logo not in the face/collar training crop if avoidable |
| **O2** | plain ivory/white tee, no logos | no prints, no stripes, no fine patterns (moiré hurts training) |
| **O3** | maroon or navy polo / overshirt | collar straight, no reflective badges |

Hair/grooming settled to the final look **before** the first take and kept identical across outfits
(no haircut, no shave change mid-session). Blot powder if the forehead gets shiny under the key.

## 3. The shot list (exact)

Angles: **F = front, 0° eye-line** (lens at eye level) · **S = side, 35°** (camera swung 35°
around the subject at the same distance and eye level). For monologues, speak to the lens (F), then
to an off-camera eye-line at 35° (S) — same words.

| ID | Outfit | Angle | Content token | Target duration | Example filename |
|----|--------|-------|---------------|-----------------|------------------|
| S01 | — | — | `roomtone`* | 30 s silence | `WTF_<date>_o1_f_roomtone_t1.wav` |
| S02 | O1 | F | `neutral` | **15 s** (8–30 s window) | `WTF_<date>_o1_f_neutral_t1.mp4` |
| S03 | O1 | S | `neutral` | 15 s | `WTF_<date>_o1_s_neutral_t1.mp4` |
| S04 | O1 | F | `hindi_mono` | **~120 s** (60–240 s window) | `WTF_<date>_o1_f_hindi_mono_t1.mp4` **+ `.wav` companion** |
| S05 | O1 | S | `hindi_mono` | ~120 s | `WTF_<date>_o1_s_hindi_mono_t1.mp4` |
| S06 | O1 | F | `still` | 3 frames | `WTF_<date>_o1_f_still_01.jpg` … `_03.jpg` |
| S07 | O2 | F | `neutral` | 15 s | `WTF_<date>_o2_f_neutral_t1.mp4` |
| S08 | O2 | S | `neutral` | 15 s | `WTF_<date>_o2_s_neutral_t1.mp4` |
| S09 | O2 | F | `english_mono` | **~120 s** (60–240 s window) | `WTF_<date>_o2_f_english_mono_t1.mp4` **+ `.wav` companion** |
| S10 | O2 | S | `english_mono` | ~120 s | `WTF_<date>_o2_s_english_mono_t1.mp4` |
| S11 | O2 | F | `still` | 3 frames | `WTF_<date>_o2_f_still_01.jpg` … `_03.jpg` |
| S12 | O3 | F | `neutral` | 15 s | `WTF_<date>_o3_f_neutral_t1.mp4` |
| S13 | O3 | S | `neutral` | 15 s | `WTF_<date>_o3_s_neutral_t1.mp4` |
| S14 | O3 | F | `casual` | **~600 s** (300–1800 s window) | `WTF_<date>_o3_f_casual_t1.mp4` **+ `.wav` companion** |
| S15 | O3 | F | `still` | 3 frames | `WTF_<date>_o3_f_still_01.jpg` … `_03.jpg` |

\* `roomtone` is intentionally not a recognised content token — intake routes it to `voice/` by
audio-extension fallback and flags `content-token-missing` as a **warning**. Expected for S01 only.

**Per-shot rules**

1. `neutral` (S02/S03/S07/S08/S12/S13): silent, looking at the lens, natural blinks and micro head
   movement, relaxed mouth. No talking, no exaggerated smiling. Record 15–20 s so 15 s can be cut.
2. `hindi_mono` (S04/S05): ~2 minutes of natural spoken Hindi about the WTF journey (see §3.1).
   Same words for both angles. One continuous take; if you flub, keep rolling and restart the
   sentence — do not stop the camera.
3. `english_mono` (S09/S10): ~2 minutes of English, same story beats as the Hindi take.
4. `casual` (S14): **~10 minutes unscripted** — talk to the lens like a podcast. Camera take for
   performance + WAV companion for voice. If only video was recorded, extract audio with ffmpeg
   **before** training (v1 intake does not extract audio). Fluency, pacing and your real voice are
   the product; the transcript does not need to be perfect. See `voice_requirements.md` §3 for the
   coverage checklist (brand names, numbers, ₹ amounts, Hinglish switches).
5. Stills (S06/S11/S15): 3 frames per outfit — straight-on, slight left turn, slight right turn;
   eyes open, neutral expression, highest still resolution the camera offers (JPG).
6. Re-takes: append `_t2`, `_t3` … to the filename; do not overwrite `_t1`. intake keeps every take.
7. Camera take and WAV companion of a shot **share the stem** (`…_t1.mp4` / `…_t1.wav`). They route
   to different buckets by design (`face_clips/` vs `voice/`); no collision.

### 3.1 Talk-track beats (keep this order for both monologues)

1. Who I am — name, founder of WTF (Gyms, Franchise, EVRYDAY, Reboot, Amplify, DigiWTF, WTF Academy).
2. How it started — the first gym, the early struggle, 2–3 concrete numbers (year, city, member counts).
3. What WTF is now — scale, team, what makes it different.
4. One lesson learned + what's next (AI systems, new brands).
Aim for ~300–340 spoken words = ~2 minutes at natural pace. Numbers and brand names are the most
valuable content for a voice clone — say them clearly, not fast.

## 4. File naming and delivery

**Convention (enforced by `intake.py`):** `WTF_<yyyymmdd>_o<1|2|3>_<f|s>_<content>[_t<take>].<ext>`

- `content` ∈ `still` · `neutral` · `hindi_mono` · `english_mono` · `casual`
- Anything that does not parse is still ingested by extension fallback but flagged in the intake
  report with `content-token-missing` so it gets a human look.
- Dump the whole card **as-is** into one folder (do not pre-sort); intake does the sorting, hashing
  and ffprobe validation, and writes the manifest.

**Expected intake verdicts for a perfectly shot session:** `errors = 0`; warnings only from S01
(`content-token-missing`) — plus, if the camera was set below spec, the matching
`resolution-below-1080p` / `fps-below-24` / `duration-out-of-range` warnings. Warnings mean "look at
this before training", not "session failed".

## 5. Self-audit

- Authored files in this package: **8** (6 package files + 2 test files; hashes in
  `evidence/file_inventory.txt`). Generated evidence files: **18**.
- Live calls: **0** — this document and the whole package contain no network code; ffprobe/ffmpeg
  are local binaries and `intake.py` defaults are offline.
- Secrets printed or stored: **0** (scan: `evidence/secret-scan.txt`).
- Unverified claims: **0** — camera/lighting/audio numbers are marked as spec-by-design; the
  duration windows and warning IDs quoted here are the live constants exercised by
  `evidence/pytest-run.txt` and demonstrated in `evidence/dry-run-demo.txt`.
- Deviations: none.
