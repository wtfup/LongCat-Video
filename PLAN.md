# WTF Avatar Factory — Master Plan

**Owner:** Vishal Nigam (approvals) · **Driver:** FITTY (Hermes) · **Date:** 2026-09-13
**Repo:** `wtfup/LongCat-Video` (WTF fork of Meituan LongCat-Video) · **Branch:** `wtf-factory`
**Infra:** AWS WTF Labs `246814138703` (us-east-1) · **Model spine:** LongCat-Video-Avatar 1.5 (MIT)

---

## 0. Objective (one line)

Unlimited, ultra-realistic **Vishal Nigam personal content** across all WTF brands — generated daily on WTF-owned infra, quality-gated, approved in a console, published to IG / YT / FB (LinkedIn manual) — everything living in this WTF-owned repository.

## 1. Architecture (the simple logic)

```
Content Brain (topics × brands × trends)
  → scripts (Hindi / English / Hinglish)
  → voice clone (TTS)
  → avatar render (LongCat-Avatar 1.5, L40S 48GB, int8+distill)
  → b-roll (open models: LongCat-Video / Wan2.2 / HunyuanVideo 1.5; fal.ai hero lane selectively)
  → edit + captions + music (ffmpeg / Remotion templates)
  → QA gate (best-of-2 + auto-score)
  → Console approval (Vishal, ~5 min/day)
  → publish queue (IG/YT/FB official APIs; LinkedIn manual)
  → performance loop → next day's content improves
```

## 2. Verified facts this plan rests on (2026-09-13)

- [x] AWS WTF Labs credits **active** — $3,422 auto-applied Sep 1–15 (Cost Explorer RECORD_TYPE proof)
- [x] GPU available: **g6e.xlarge = 1× L40S 48GB**; quota headroom (G-family 8 vCPU, serving box uses 4); EC2 DryRun = "would have succeeded"
- [x] **LongCat-Video-Avatar 1.5 beats HeyGen in human eval** (770 raters, 500+ cases): win rate 54.3% vs HeyGen, 61.1% vs OmniHuman 1.5, 65.9% vs Kling Avatar. MIT license. Runs on single L40S 48GB (int8 + distill, community-verified).
- [x] WTF fork created: `wtfup/LongCat-Video` (this repo), branch `wtf-factory`

## 3. Phase 1 — Pilot infra (TODAY)

- [ ] Launch `wtf-avatar-factory-pilot`: g6e.xlarge, DLAMI Ubuntu+NVIDIA, **300GB gp3**, SSM-only access, **zero public ingress**
- [ ] S3 bucket `wtf-avatar-factory-246814138703` + IAM role `wtf-avatar-factory-role` (SSM core + S3 RW)
- [ ] Quota request: G-family 8 → 32 vCPU (scale to 2–4 boxes)
- [ ] Deploy stack on box: LongCat-Avatar 1.5 (int8+distill), voice clone (F5-TTS/XTTS), ffmpeg/edit deps
- [ ] **Benchmark = real numbers**: VRAM fit + measured sec-per-10s-render + GPU-hours-per-hour → lock throughput table

## 4. Phase 2 — Capture (this week — Vishal: 30–45 min, one time)

- [ ] Shot-list from `wtf/capture-kit/SHOT_LIST.md` (3 outfits, 2 angles, Hindi + English monologue, 10-min casual talk = voice corpus)
- [ ] Intake → reference assets + voice corpus + training set
- [ ] Train identity LoRA (realism boost)

## 5. Phase 3 — Factory build (kanban swarm — LAUNCHED TODAY)

Workers W1–W7 (contracts in `BRIEF.md`): factory-core queue · console v1 · render-kit · content-brain · publishers (dark) · capture-kit · QA gate
Every package: code + tests green + evidence + self-audit. Verifier re-executes; synthesizer rolls up with gaps.

## 6. Phase 4 — Pilot content & realism proof

- [ ] 3 test videos (Hindi / English / Hinglish) + **1 fully-produced 45s short**
- [ ] Blind panel ("AI hai ya nahi?") vs HeyGen same-script comparison
- [ ] Receipt: measured throughput + quality scores → **GO/NO-GO on scale**

## 7. Phase 5 — Scale

- [ ] 2–4 render boxes (quota 32) · daily rhythm 10–25 posts · approval console · performance loop
- [ ] Expand: WTF verticals (Gyms, EVRYDAY, Reboot, Amplify, Acadmey) · languages · 50 avatar-variants matrix (look × language × format)

## 8. Gates & safety (non-negotiable)

- Publishing to any channel = **explicit per-channel approval**; default = approval-console ON; **LinkedIn manual always**
- No customer outbound, no live sends, no paid hero-lane spend without approval
- Workers: zero git / zero AWS / zero live calls (dark defaults)
- Rollback: `terminate-instances` (auto-delete volume) · S3/IAM deletable · fork is ours

## 9. Costs (verified rates)

- g6e.xlarge **$1.861/hr** ≈ $1,358/mo 24×7 — **credits-covered** · 300GB gp3 ≈ $24/mo · fal.ai hero lane ad-hoc ≈ $20–100/mo at planned volume

## 10. Ownership

- **FITTY:** everything technical — infra, builds, deploy, ops, content engine, reporting
- **Vishal:** record once (shot-list) · approve publishes · decide hero-lane spends
