# CAPTURE_CHECKLIST.md — W6 Capture Kit

Day-of checklist for the Vishal capture session. Specs live in `SHOT_LIST.md`; this file is the
operational run-sheet: gear, settings, running order, offload, intake command.

## 1. Before the session (T-1 day)

- [ ] **Gear:** camera with 4K ≥ 25fps · tripod · lav kit (DJI Mic / Rode Wireless class) + spare
      batteries + charging cables · soft key light + fill/reflector · blot powder.
- [ ] **Storage:** ≥ 64 GB free on card(s). Budget: 4K @ 100 Mbps ≈ 750 MB/min → a ~25-minute
      rolling session ≈ 19 GB raw. Carry a second card.
- [ ] **Room booked:** quiet, soft furnishings, no AC/fan noise, door closed, windows covered
      (no backlight). Phone(s) on silent, assistant briefed to stay out of frame and quiet.
- [ ] **Wardrobe (3 outfits) steamed:** O1 WTF black tee · O2 plain ivory/white tee · O3 maroon or
      navy polo. No stripes/fine patterns, no reflective logos.
- [ ] **Content prep:** read `SHOT_LIST.md` §3.1 talk-track beats once; do not memorise — natural
      delivery is the product. No teleprompter unless it can be placed lens-level (eye-line must stay true).
- [ ] **Folder prepared:** `RAW_DIR=/Users/vishalnigammacminioffice/Downloads/WTF_CAPTURE_<yyyymmdd>`
      (created, empty). The card contents copy into this folder **as-is** later (§4).

## 2. Settings sheet — set once, verify on screen

| # | Setting | Value | Verified |
|---|---------|-------|----------|
| 1 | Resolution | 3840×2160 (floor 1920×1080) | [ ] |
| 2 | Frame rate | 25 or 30 fps | [ ] |
| 3 | Shutter | 1/50 (25fps) or 1/60 (30fps) | [ ] |
| 4 | Aperture | f/2.8–f/4 | [ ] |
| 5 | ISO | lowest correct exposure (≤ 1600) | [ ] |
| 6 | White balance | MANUAL 5600K (AWB off) | [ ] |
| 7 | Focus | manual, locked on eyes (AF off) | [ ] |
| 8 | Beauty / HDR / filters | OFF | [ ] |
| 9 | Bitrate / codec | ≥ 100 Mbps H.264/H.265 (or ProRes) | [ ] |
| 10 | Audio | lav 15–20 cm below chin, 48 kHz, avg −18…−12 dBFS, peaks ≤ −6 dBFS | [ ] |
| 11 | Frame check | chest-up, eyes upper-third, 5–10 % headroom, 1.2–1.5 m | [ ] |
| 12 | Lights | key 45° left / fill right (2:1), background 1–1.5 stops dark | [ ] |

Record a 20-second test clip and **play it back on a bigger screen** before outfit change #1.
Wrong settings discovered after the session = session redone.

## 3. Running order (checkboxes = one session)

Slate every shot: clap once in frame (or say "take <n>, outfit <n>, <content>" aloud) — free sync marker.

- [ ] **S01** Room tone: 30 s standing still, eyes open, silent → `WTF_<date>_o1_f_roomtone_t1.wav`
- [ ] **O1 (WTF black tee)**
  - [ ] S02 neutral 15 s, front — [ ] S03 neutral 15 s, side 35°
  - [ ] S04 hindi_mono ~120 s, front + WAV companion — [ ] S05 hindi_mono ~120 s, side
  - [ ] S06 stills: 3 frames (straight / slight left / slight right)
- [ ] **O2 (ivory tee)**
  - [ ] S07 neutral 15 s, front — [ ] S08 neutral 15 s, side
  - [ ] S09 english_mono ~120 s, front + WAV companion — [ ] S10 english_mono ~120 s, side
  - [ ] S11 stills: 3 frames
- [ ] **O3 (maroon/navy polo)**
  - [ ] S12 neutral 15 s, front — [ ] S13 neutral 15 s, side
  - [ ] S14 casual ~600 s, front (video) + WAV companion
  - [ ] S15 stills: 3 frames

**After every shot (20-second QC):** play back 5 s → focus on eyes? exposure stable? audio meter
moved? filename spelled per §4 of `SHOT_LIST.md`? If any check fails: re-shoot immediately as `_t2`.

**Cut discipline:** one continuous take per shot even with flubs (restart the sentence, keep
rolling). Only stop for wardrobe/lighting changes. No cuts inside `neutral` clips.

## 4. Offload + intake (exact commands — local only, no network)

```bash
# 1) Copy the card contents AS-IS into the prepared folder (never work off the card)
RAW_DIR=/Users/vishalnigammacminioffice/Downloads/WTF_CAPTURE_$(date +%Y%m%d)
mkdir -p "$RAW_DIR" && cp -R /Volumes/<CARD>/* "$RAW_DIR"/

# 2) DRY RUN first — plan + validate, writes nothing
python3 /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/capture-kit/intake.py \
  --source "$RAW_DIR" \
  --dest   /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data \
  --dry-run

# 3) Review the plan: every S01–S15 present, bucket correct, warnings understood.
#    Then the real run (copy by default; camera originals stay in RAW_DIR):
python3 /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/capture-kit/intake.py \
  --source "$RAW_DIR" \
  --dest   /Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data
```

- [ ] Run exit code is **0** (`echo $?`) — exit 1 means errors listed; fix, re-run (idempotent).
- [ ] `wtf/_data/manifest.json` exists and its summary shows `errors: 0`.
- [ ] Spot-check: `ls wtf/_data/assets/{refs,voice,face_clips}` — refs ≥ **15** files (9 stills +
      6 neutral clips), voice ≥ **4** files (hindi WAV, english WAV, casual WAV, room tone),
      face_clips ≥ **5** files (hindi ×2 angles, english ×2 angles, casual ×1).
- [ ] **Only now** format/reuse the camera card.

**Warning policy:** warnings (`duration-out-of-range`, `resolution-below-1080p`, `fps-below-24`,
`content-token-missing`, `sample-rate-below-44k`, `not-mono`) are review prompts. Re-shoot only if a
**required** shot is out of spec (e.g. a `neutral` clip under 8 s, a monologue under 60 s, wrong
resolution). `roomtone`'s `content-token-missing` warning is expected.

## 5. Self-audit

- Authored files in this package: **8** (6 package files + 2 test files; hashes in
  `evidence/file_inventory.txt`). Generated evidence files: **18**.
- Live calls: **0** — every command in this checklist is local (`cp`, `ls`, `date`, `python3`);
  `intake.py` has no network code and defaults to offline validation via local ffprobe.
- Secrets printed or stored: **0** (scan: `evidence/secret-scan.txt`).
- Unverified claims: **0** — checklist items map to `SHOT_LIST.md` §2/§3; the intake commands are
  the same ones executed in `evidence/dry-run-demo.txt` and `evidence/real-run-demo.txt`; bucket
  spot-check counts are arithmetic over the S01–S15 shot table.
- Deviations: none.
