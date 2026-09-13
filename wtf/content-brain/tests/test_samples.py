"""The 10 sample briefs are deliverables: structure, coverage, reproducibility."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

import brain

PKG = Path(__file__).resolve().parents[1]
SAMPLES = PKG / "samples"

REQUIRED_SECTIONS = (
    "## Hook",
    "## 45s Script — English",
    "## 45s Script — Hindi",
    "## B-Roll Shot List",
    "## Caption + Hashtags",
    "## Thumbnail Idea",
)

# Calibrated to the measured authored library (68-101 words per language);
# see tests/test_brain.py for the rationale.
WORD_BOUNDS = (60, 160)


@pytest.fixture(scope="module")
def index():
    return json.loads((SAMPLES / "index.json").read_text(encoding="utf-8"))


def test_exactly_ten_samples_and_index(index):
    files = sorted(p.name for p in SAMPLES.glob("brief_*.md"))
    assert len(files) == 10
    assert len(index["briefs"]) == 10
    assert [e["file"] for e in index["briefs"]] == files


def test_index_covers_all_pillars_and_topics(index):
    entries = index["briefs"]
    pillars = [e["pillar"] for e in entries]
    from collections import Counter

    assert Counter(pillars) == Counter({
        "ai_systems": 2, "founder_journey": 2, "wtf_brands": 2,
        "fitness_transformation": 2, "business": 2,
    })
    topic_ids = [e["topic_id"] for e in entries]
    assert len(set(topic_ids)) == 10


def test_index_metadata_is_complete(index):
    for e in index["briefs"]:
        assert e["brief_id"].startswith("cb-")
        assert e["date"] and e["brand"] and e["title_en"] and e["title_hi"]
        assert e["languages"] == ["en", "hi"]
        assert re.fullmatch(r"[0-9a-f]{64}", e["sha256"])
        for lang in ("en", "hi"):
            assert WORD_BOUNDS[0] <= e["word_counts"][lang] <= WORD_BOUNDS[1]


def test_each_sample_has_all_sections_and_hashes_match(index):
    for e in index["briefs"]:
        path = SAMPLES / e["file"]
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == e["sha256"], e["file"]
        text = raw.decode("utf-8")
        for heading in REQUIRED_SECTIONS:
            assert heading in text, (e["file"], heading)
        assert e["brief_id"] in text
        assert e["title_hi"] in text


def test_samples_are_deterministically_regenerable(tmp_path):
    entries = brain.write_samples(tmp_path, start=date(2026, 9, 14))
    assert len(entries) == 10
    for name in sorted(p.name for p in SAMPLES.glob("*.md")):
        assert (tmp_path / name).read_bytes() == (SAMPLES / name).read_bytes(), name
    assert (tmp_path / "index.json").read_bytes() == (SAMPLES / "index.json").read_bytes()


def test_samples_dates_are_consecutive(index):
    days = [date.fromisoformat(e["date"]) for e in index["briefs"]]
    assert days == sorted(days)
    for prev, nxt in zip(days, days[1:]):
        assert (nxt - prev).days == 1
