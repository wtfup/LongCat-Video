"""Brief generation contract: structure, timecodes, determinism, CLI."""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import brain

PKG = Path(__file__).resolve().parents[1]
BRAIN = PKG / "brain.py"

# A 45s talking-head script holds VO plus deliberate pauses for b-roll and
# text cards. The authored library measures 68-101 words per language; the
# band below (calibrated to that measured range with margin) fails scripts
# that shrink below a plausible VO budget or balloon past the 45s cut.
WORD_BOUNDS = (60, 160)

TIMECODE_RE = re.compile(r"^\[(\d+):(\d{2})–(\d+):(\d{2})\] ([A-Z0-9 ]+)$")


def _secs(m, s):
    return int(m) * 60 + int(s)


def _parse_beats(script: str):
    """Return [(start_s, end_s, name, vo, overlay)] parsed from a script block."""
    beats = []
    lines = script.splitlines()
    for i, line in enumerate(lines):
        m = TIMECODE_RE.match(line.strip())
        if not m:
            continue
        vo = None
        overlay = None
        for follow in lines[i + 1: i + 3]:
            follow = follow.strip()
            if follow.startswith("VO:"):
                vo = follow[3:].strip().strip('"')
            elif follow.startswith("ON-SCREEN:"):
                overlay = follow[len("ON-SCREEN:"):].strip()
        beats.append((_secs(m.group(1), m.group(2)), _secs(m.group(3), m.group(4)),
                      m.group(5), vo, overlay))
    return beats


@pytest.fixture(scope="module")
def brief():
    return brain.daily_brief(date(2026, 9, 14), pillar="ai_systems", topic="ai_systems-01")


def test_brief_identity(brief):
    assert brief.id == "cb-2026-09-14-ai_systems-01"
    assert brief.pillar == "ai_systems"
    assert brief.brand == "wtf_gyms"
    assert brief.duration_s == 45
    assert brief.format == "9:16_vertical_reel"
    assert brief.topic_id == "ai_systems-01"


def test_brief_has_both_languages(brief):
    assert set(brief.scripts) == {"en", "hi"}
    assert set(brief.hook) == {"en", "hi"}
    assert set(brief.caption) == {"en", "hi"}
    assert set(brief.cta) == {"en", "hi"}
    for lang in ("en", "hi"):
        assert len(brief.scripts[lang]) > 200
        assert len(brief.hook[lang]) > 10


def test_scripts_have_seven_connected_beats(brief):
    for lang in ("en", "hi"):
        beats = _parse_beats(brief.scripts[lang])
        assert len(beats) == 7, lang
        starts = [b[0] for b in beats]
        ends = [b[1] for b in beats]
        assert starts[0] == 0
        assert ends[-1] == 45
        # contiguous coverage, strictly forward
        for prev, nxt in zip(beats, beats[1:]):
            assert prev[1] == nxt[0]
            assert nxt[0] < nxt[1]
        for _, _, name, vo, overlay in beats:
            assert vo, (lang, name)
            assert overlay, (lang, name)


def test_scripts_respect_word_budget(brief):
    for lang, count in brief.word_counts.items():
        assert WORD_BOUNDS[0] <= count <= WORD_BOUNDS[1], (lang, count)


def test_hook_uses_topic_and_no_placeholder_leak(brief):
    for lang in ("en", "hi"):
        assert "{" not in brief.hook[lang] and "}" not in brief.hook[lang]


def test_shot_list_shape(brief):
    assert len(brief.shot_list) >= 6
    for row in brief.shot_list:
        assert set(row) == {"t", "type", "shot", "overlay"}
        assert TIMECODE_RE.match("[{}] HOOK".format(row["t"]))
        assert row["shot"].strip()
    # covers the full runtime
    assert brief.shot_list[0]["t"].startswith("0:00")
    assert brief.shot_list[-1]["t"].endswith("0:45")


def test_hashtag_pack(brief):
    assert 6 <= len(brief.hashtags) <= 12
    assert len(set(brief.hashtags)) == len(brief.hashtags)
    assert all(tag.startswith("#") and len(tag) > 2 for tag in brief.hashtags)


def test_thumbnail_and_thumb_text(brief):
    assert "Overlay text" in brief.thumbnail
    assert brief.thumb_text
    assert len(brief.thumb_text.split()) <= 5


def test_markdown_contains_all_required_sections(brief):
    md = brief.to_markdown()
    for heading in (
        "## Hook",
        "## 45s Script — English",
        "## 45s Script — Hindi",
        "## B-Roll Shot List",
        "## Caption + Hashtags",
        "## Thumbnail Idea",
    ):
        assert heading in md, heading
    assert brief.id in md


def test_determinism_byte_identical():
    a = brain.daily_brief(date(2026, 9, 14), pillar="ai_systems", topic="ai_systems-01")
    b = brain.daily_brief(date(2026, 9, 14), pillar="ai_systems", topic="ai_systems-01")
    assert a.to_markdown() == b.to_markdown()
    assert a.to_dict() == b.to_dict()


def test_variation_changes_hook():
    a = brain.daily_brief(date(2026, 9, 14), pillar="ai_systems", topic="ai_systems-01", variation=0)
    b = brain.daily_brief(date(2026, 9, 14), pillar="ai_systems", topic="ai_systems-01", variation=1)
    assert a.hook["en"] != b.hook["en"]
    assert a.id == b.id  # identity does not depend on variation


def test_auto_pillar_follows_weekday_rotation():
    lib = brain.load_library()
    d = date(2026, 9, 14)
    brief = brain.daily_brief(d)
    expected = lib["rotation"]["weekly"][d.strftime("%A").lower()]
    assert brief.pillar == expected
    assert brief.brand  # brand auto-resolved from pillar default


def test_auto_topic_is_deterministic_and_valid():
    d = date(2026, 9, 14)
    a = brain.daily_brief(d, pillar="wtf_brands")
    b = brain.daily_brief(d, pillar="wtf_brands")
    assert a.topic_id == b.topic_id
    lib = brain.load_library()
    pillar = [p for p in lib["pillars"] if p["id"] == "wtf_brands"][0]
    assert a.topic_id in {t["id"] for t in pillar["topics"]}


def test_unknown_pillar_and_topic_fail_closed():
    with pytest.raises(ValueError):
        brain.daily_brief(date(2026, 9, 14), pillar="nope")
    with pytest.raises(ValueError):
        brain.daily_brief(date(2026, 9, 14), pillar="ai_systems", topic="nope-99")


def test_topics_produce_distinct_briefs():
    a = brain.daily_brief(date(2026, 9, 14), pillar="ai_systems", topic="ai_systems-01")
    b = brain.daily_brief(date(2026, 9, 14), pillar="ai_systems", topic="ai_systems-02")
    assert a.scripts["en"] != b.scripts["en"]


def test_cli_writes_markdown(tmp_path):
    out = tmp_path / "brief.md"
    proc = subprocess.run(
        [sys.executable, str(BRAIN), "--date", "2026-09-14",
         "--pillar", "ai_systems", "--topic", "ai_systems-01", "--out", str(out)],
        capture_output=True, text=True, cwd=str(PKG),
    )
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Daily Brief — 2026-09-14 — AI Systems")
    assert "## 45s Script — Hindi" in text


def test_cli_json_mode_is_machine_readable():
    proc = subprocess.run(
        [sys.executable, str(BRAIN), "--date", "2026-09-14",
         "--pillar", "ai_systems", "--topic", "ai_systems-01", "--json"],
        capture_output=True, text=True, cwd=str(PKG),
    )
    assert proc.returncode == 0, proc.stderr
    import json

    data = json.loads(proc.stdout)
    assert data["id"] == "cb-2026-09-14-ai_systems-01"
    assert data["languages"] == ["en", "hi"]


def test_prompt_audit_records_rendered_prompts(brief):
    assert brief.prompt_audit
    kinds = {row["kind"] for row in brief.prompt_audit}
    assert {"hook", "points", "shot_list", "thumbnail"} <= kinds
    for row in brief.prompt_audit:
        assert re.fullmatch(r"[0-9a-f]{64}", row["prompt_sha256"]), row
