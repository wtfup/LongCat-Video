"""Pillar library contract tests (pillars.yaml)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import brain

PKG = Path(__file__).resolve().parents[1]
PILLARS_FILE = PKG / "pillars.yaml"

EXPECTED_PILLARS = {
    "ai_systems",
    "founder_journey",
    "wtf_brands",
    "fitness_transformation",
    "business",
}

PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
ALLOWED_PLACEHOLDERS = {"topic", "topic_lc", "brand"}


@pytest.fixture(scope="module")
def lib():
    return brain.load_library(PILLARS_FILE)


def _all_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _all_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_strings(v)


def test_library_top_level_shape(lib):
    assert set(lib["pillars_ids"] if "pillars_ids" in lib else [p["id"] for p in lib["pillars"]]) == EXPECTED_PILLARS
    assert lib["languages"] == ["hi", "en"]
    assert lib["format"]["aspect"] == "9:16"
    assert int(lib["format"]["duration_s"]) == 45


def test_five_pillars_two_topics_each(lib):
    assert len(lib["pillars"]) == 5
    for pillar in lib["pillars"]:
        assert len(pillar["topics"]) == 2, pillar["id"]
        assert pillar["label"] and pillar["description_en"]
        assert pillar["default_brand"] in {b["id"] for b in lib["brands"]}


def test_brands_cover_wtf_ecosystem(lib):
    brand_ids = {b["id"] for b in lib["brands"]}
    for required in ("wtf_gyms", "wtf_franchise", "everyday", "reboot", "amplify", "wtf_academy"):
        assert required in brand_ids
    for brand in lib["brands"]:
        assert brand["label"] and brand["hashtags"] and brand["hashtags"][0].startswith("#")


def test_topic_fields_complete(lib):
    for pillar in lib["pillars"]:
        for topic in pillar["topics"]:
            where = topic["id"]
            assert topic["id"].startswith(pillar["id"] + "-"), where
            for key in ("title_en", "title_hi", "angle_en", "angle_hi", "proof_en", "proof_hi"):
                assert topic[key].strip(), (where, key)
            for key in ("points_en", "points_hi"):
                assert len(topic[key]) == 3 and all(p.strip() for p in topic[key]), (where, key)
            assert len(topic["props"]) >= 3, where
            assert 1 <= len(topic["thumb_text"].split()) <= 5, where


def test_topic_ids_unique(lib):
    ids = [t["id"] for p in lib["pillars"] for t in p["topics"]]
    assert len(ids) == len(set(ids)) == 10


def test_rotation_covers_every_pillar_in_a_week(lib):
    weekly = lib["rotation"]["weekly"]
    assert len(weekly) == 7
    assert set(weekly.values()) == EXPECTED_PILLARS
    for day, pillar in weekly.items():
        assert day in {
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        }
    secondary = lib["rotation"]["secondary"]
    assert set(secondary) == EXPECTED_PILLARS


def test_hooks_contexts_ctas(lib):
    for pillar in lib["pillars"]:
        for lang in ("en", "hi"):
            hooks = pillar["hook_templates_" + lang]
            assert len(hooks) >= 3
            assert all(("{topic}" in h or "{topic_lc}" in h) for h in hooks), (pillar["id"], lang)
            assert len(pillar["context_templates_" + lang]) >= 2
            assert len(pillar["cta_templates_" + lang]) >= 3


def test_placeholder_whitelist(lib):
    """Only {topic} and {brand} may appear anywhere in the library."""
    for s in _all_strings(lib):
        for match in PLACEHOLDER_RE.findall(s):
            assert match in ALLOWED_PLACEHOLDERS, (match, s)


def test_no_unquoted_secret_like_tokens(lib):
    blob = PILLARS_FILE.read_text(encoding="utf-8")
    for pat in ("AKIA", "ghp_", "-----BEGIN", "sk-live"):
        assert pat not in blob


def test_mini_yaml_matches_pyyaml():
    """The stdlib-only fallback parser must agree with PyYAML exactly."""
    yaml = pytest.importorskip("yaml")
    text = PILLARS_FILE.read_text(encoding="utf-8")
    assert brain.parse_mini_yaml(text) == yaml.safe_load(text)
