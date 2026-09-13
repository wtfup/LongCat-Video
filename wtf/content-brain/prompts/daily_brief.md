# Daily Brief — generation instruction (interface stub)

Build ONE daily brief for {date} (variation {variation}).

Inputs:
- Pillar: {pillar_label} ({pillar})
- Brand: {brand_label} ({brand})
- Topic: {topic}
- Format: {format}, {duration_s} seconds
- Languages: {language_label}

Deliver all six components, in this order:
1. **hook** — the first-3-seconds line, one sentence.
2. **script** — a {duration_s}s script with seven timecoded beats.
3. **shot** — a b-roll shot list, one row per beat, with on-screen text.
4. **caption** — per language, three short lines: topic line, angle line, CTA.
5. **hashtag** — 6–12 tags: brand first, then pillar, then WTF core tags.
6. **thumbnail** — one concept line + overlay text (max 5 words) + expression.

Output contract (interface stub, no live model in v1):
- Return structured plain text; one component per section.
- No links, no phone numbers, no promotional prices, no unverified claims.
- If a fact is missing, say so in the Production Notes — never invent it.
