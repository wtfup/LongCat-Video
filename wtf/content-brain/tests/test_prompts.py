"""Prompt template library: presence, slot coverage, safe rendering."""
from __future__ import annotations

import re
from pathlib import Path

import brain
import providers

PKG = Path(__file__).resolve().parents[1]
PROMPTS = PKG / "prompts"

REQUIRED_PROMPTS = (
    "system.md",
    "daily_brief.md",
    "script_45s_en.md",
    "script_45s_hi.md",
    "shot_list.md",
    "caption_hashtags.md",
    "thumbnail.md",
)

PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

# Canonical slot vocabulary available to every prompt template.
CANONICAL_SLOTS = {
    "topic", "topic_hi", "topic_en", "pillar", "pillar_label", "brand", "brand_label",
    "language", "language_label", "duration_s", "format", "angle", "hook", "context",
    "points", "proof", "cta", "beats", "n_beats", "props", "hashtags", "thumb_text",
    "variation", "date",
}


def test_all_prompt_files_present():
    for name in REQUIRED_PROMPTS:
        assert (PROMPTS / name).is_file(), name


def test_templates_render_and_use_only_canonical_slots():
    slots = {k: "X" for k in CANONICAL_SLOTS}
    slots["language_label"] = "Hindi"
    slots["beats"] = "- b1"
    slots["points"] = "- p1"
    for name in REQUIRED_PROMPTS:
        text = (PROMPTS / name).read_text(encoding="utf-8")
        used = set(PLACEHOLDER_RE.findall(text))
        assert used, name  # every template must be parameterized
        assert used <= CANONICAL_SLOTS, (name, used - CANONICAL_SLOTS)
        rendered = providers.render_template(text, slots)
        assert rendered.strip(), name
        assert "{" not in rendered, name


def test_system_prompt_defines_voice_guardrails():
    text = (PROMPTS / "system.md").read_text(encoding="utf-8")
    for phrase in ("Hormozi", "hook", "Hindi", "English", "20", "no fabricated"):
        assert phrase.lower() in text.lower(), phrase


def test_daily_brief_prompt_requests_all_components():
    text = (PROMPTS / "daily_brief.md").read_text(encoding="utf-8").lower()
    for component in ("hook", "script", "shot", "caption", "hashtag", "thumbnail"):
        assert component in text


def test_prompt_audit_points_at_real_files(tmp_path):
    from datetime import date

    brief = brain.daily_brief(date(2026, 9, 14), pillar="business", topic="business-01")
    for row in brief.prompt_audit:
        assert (PKG / row["template"]).is_file(), row
