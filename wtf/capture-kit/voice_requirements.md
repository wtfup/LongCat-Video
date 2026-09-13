# voice_requirements.md — W6 Capture Kit

Requirements for the voice corpus that feeds WTF's voice clone (F5-TTS / XTTS-class models on the
pilot box; see `PLAN.md` §3) and the avatar lip-sync reference. Companion docs: `SHOT_LIST.md`
(recording spec) and `CAPTURE_CHECKLIST.md` (run-sheet).

## 1. What the corpus is for

| Consumer | Needs | Comes from |
|---|---|---|
| Voice clone / TTS | ≥ 10 min clean single-speaker speech; ideally 15–20 min | S14 casual talk; S04/S09 monologue WAVs |
| Lip-sync + avatar fine-tune | matching video of the same speech | S04/S05/S09/S10/S14 camera takes |
| Silence model / room tone | 30 s ambience for gap filling | S01 |

Minimum for v1: **≥ 14 minutes** of usable speech (2 + 2 + 10). Target **≥ 20 minutes** — the extra
comes free from keeping the recorder running through re-takes and setup chat *if* you want it
(don't force it; unusable audio hurts more than it helps).

## 2. Audio spec — exact

| Property | Required | Notes |
|---|---|---|
| Sample rate | **48 kHz** (44.1 kHz acceptable floor) | intake warns below 44 100 Hz (`sample-rate-below-44k`) |
| Bit depth | 24-bit preferred, 16-bit acceptable | WAV preferred over compressed |
| Channels | **mono** (1) | stereo accepted with a `not-mono` info note; do not sum/remix before training |
| Level | average **−18 to −12 dBFS**, peaks **≤ −6 dBFS** | no clipping, no limiter pumping |
| Format | WAV (companion recordings) / lossless | MP3/AAC only as emergency backup |
| Mic | lavalier 15–20 cm below chin, fixed distance per session | changing distance changes timbre |
| Noise | no music, TV, fan/AC, traffic, notifications | noise floor should sit well below speech |
| Space | soft room, minimal echo | no bathroom/kitchen/marble rooms; curtains & rugs help |
| Take rule | one continuous take per shot | stop/start edits create corpus seams |

**What intake validates automatically:** duration window per content token, sample rate ≥ 44.1 kHz,
channel count, and that the file decodes at all (ffprobe). **What it cannot validate:** noise floor,
reverb, intelligibility, accent coverage — those are listening checks, done once after intake by
playing back the casual take.

## 3. Content requirements (this is the part that decides clone quality)

**Primary corpus — S14 casual talk (~10 min, unscripted).** Speak like a podcast, not a presenter.
Cover these beats inside the 10 minutes (any order, natural):

- [ ] Self-introduction, name, role, day-to-day work (simple sentences first — they anchor the timbre)
- [ ] WTF story: first gym → today; at least 3 concrete numbers (years, cities, member counts)
- [ ] **Brand names said clearly:** WTF · WTF Gyms · WTF Franchise · EVRYDAY · Reboot · Amplify ·
      DigiWTF · WTF Academy · WTF 360
- [ ] **Money & numbers:** ₹ amounts (₹1,000 / ₹21,000 / ₹1 lakh / ₹1 crore), dates, phone-style
      digit strings, percentages — clones fail first on numbers, double-train them
- [ ] **Hinglish naturally:** at least a few sentences that start in English and finish in Hindi
      (and vice-versa); do not perform it — code-switch the way you actually talk
- [ ] Emotion range: calm explanation, a laugh, mild emphasis — flat reads clone flat
- [ ] Do NOT read on camera (read speech clones badly); bullet points on the wall are fine

**Secondary corpus — S04 Hindi 2-min + S09 English 2-min monologues** (same story beats,
`SHOT_LIST.md` §3.1). These give the clone clean parallel content across languages. Record each as
a WAV companion + camera take.

**Do-not list:** singing (unless wanted later), whispering games, deliberate accents you don't have,
long dead air (> 3 s), overlapping voices, phone calls played over the speaker.

## 4. Delivery, pairing and downstream handoff

- Files land in `assets/voice/` via `intake.py` using the standard naming convention
  (`WTF_<date>_o<n>_<f|s>_<content>_t<n>.wav`).
- **Transcript pairing (expected input for training, produced later — not by v1 intake):**
  `assets/voice/transcripts.jsonl`, one JSON object per file, ≤ 30 s per segment for short-file
  training recipes:

  ```json
  {"file": "WTF_20260914_o3_f_casual_t1.wav", "language": "hi-en", "text": "..."}
  {"file": "WTF_20260914_o1_f_hindi_mono_t1.wav", "language": "hi", "text": "..."}
  {"file": "WTF_20260914_o2_f_english_mono_t1.wav", "language": "en", "text": "..."}
  ```

  Transcription itself happens on the box (faster-whisper — the QA gate W7 stubs the same
  interface); v1 does not transcribe.
- The 10-min casual take is the acceptance reference: listen to 60 s of the final intake copy once.
  If it has hiss, clipping or reverb, fix the first 30 seconds of the next session — do not "fix in
  post" for training data.

## 5. Self-audit

- Authored files in this package: **8** (6 package files + 2 test files; hashes in
  `evidence/file_inventory.txt`). Generated evidence files: **18**.
- Live calls: **0** — this document prescribes local recording/validation only; `intake.py` (the
  only executable here) makes no network calls and defaults to offline validation.
- Secrets printed or stored: **0** (scan: `evidence/secret-scan.txt`).
- Unverified claims: **0** — dBFS/sample-rate/channel numbers are spec-by-design (recording
  guidance); the two enforced thresholds quoted (44.1 kHz warning, mono note) are live constants
  verified by `evidence/pytest-run.txt`; the transcript-format line is marked as a downstream
  expectation, not an existing artifact.
- Deviations: none.
