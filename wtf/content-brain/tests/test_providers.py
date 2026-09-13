"""Provider interface tests: dark by default, deterministic stub, live closed."""
from __future__ import annotations

import json

import pytest

import providers
from providers import (
    GenerationRequest,
    KINDS,
    LiveDisabled,
    LiveNotWired,
    LiveProvider,
    ProviderError,
    StubProvider,
    get_provider,
    render_template,
)

SLOTS = {
    "topic": "AI receptionist",
    "brand_label": "WTF Gyms",
    "hooks": ["A: {topic} now.", "B: nobody says this about {topic}.", "C: {topic} in 45s."],
    "contexts": ["ctx one", "ctx two"],
    "points": ["p1", "p2", "p3"],
    "proof": "proof line",
    "ctas": ["cta one", "cta two", "cta three"],
    "title": "Title line",
    "angle": "angle line",
    "cta_line": "cta line",
    "hashtag_pool": ["#A", "#B", "#A", "#C", "#D"],
    "max_hashtags": 12,
    "concepts": ["dark studio close-up", "gym floor walk-in"],
    "thumb_text": "REPLIES IN 60 SEC",
    "expression": "confident, mid-sentence",
    "beats": [
        {"t": "0:00–0:03", "type": "A-roll", "shot": "tight close-up", "overlay": "HOOK"},
        {"t": "0:03–0:11", "type": "A-roll+b-roll", "shot": "walk-and-talk", "overlay": "CTX"},
    ],
}


def _req(kind, variation=0, slots=None, lang="en"):
    slots = dict(SLOTS if slots is None else slots)
    return GenerationRequest(
        kind=kind,
        slots=slots,
        prompt_template="prompts/x.md",
        prompt_text="rendered prompt for " + kind + " " + lang,
        variation=variation,
    )


def test_kind_catalog_complete():
    assert set(KINDS) == {
        "hook", "context", "points", "proof", "cta",
        "caption", "hashtags", "thumbnail", "shot_list",
    }


def test_default_provider_is_dark_stub():
    p = get_provider()
    assert isinstance(p, StubProvider)
    assert p.live is False
    assert p.name == "stub"


def test_live_requires_env_flag():
    with pytest.raises(LiveDisabled):
        get_provider(live=True, env={})
    with pytest.raises(LiveDisabled):
        get_provider(live=True, env={"CONTENT_BRAIN_LIVE": "0"})


def test_live_is_not_wired_even_when_enabled():
    p = get_provider(live=True, env={"CONTENT_BRAIN_LIVE": "1"})
    assert isinstance(p, LiveProvider)
    assert p.live is True
    with pytest.raises(LiveNotWired):
        p.generate(_req("hook"))


def test_unknown_provider_name_rejected():
    with pytest.raises(ProviderError):
        get_provider(name="definitely-not-a-provider")


def test_stub_is_deterministic():
    p = StubProvider()
    for kind in KINDS:
        a = p.generate(_req(kind))
        b = p.generate(_req(kind))
        assert a == b, kind


def test_variation_rotates_selection():
    p = StubProvider()
    hooks = {p.generate(_req("hook", v)) for v in range(3)}
    assert len(hooks) == 3  # 3 distinct templates in the fixture


def test_hook_fills_topic_and_leaves_no_placeholder():
    out = StubProvider().generate(_req("hook"))
    assert "AI receptionist" in out
    assert "{" not in out and "}" not in out


def test_points_and_hashtags_are_valid_json():
    p = StubProvider()
    points = json.loads(p.generate(_req("points")))
    assert points == ["p1", "p2", "p3"]
    tags = json.loads(p.generate(_req("hashtags")))
    assert tags == ["#A", "#B", "#C", "#D"]  # dedupe preserves order


def test_points_validation_fails_closed():
    with pytest.raises(ProviderError):
        StubProvider().generate(_req("points", slots={"points": ["only-one"]}))
    with pytest.raises(ProviderError):
        StubProvider().generate(_req("points", slots={"points": "not-a-list"}))


def test_hashtags_cap_applied():
    many = ["#t{}".format(i) for i in range(20)]
    req = _req("hashtags", slots={"hashtag_pool": many, "max_hashtags": 5})
    assert len(json.loads(StubProvider().generate(req))) == 5


def test_shot_list_valid_json_and_shape():
    rows = json.loads(StubProvider().generate(_req("shot_list")))
    assert [r["t"] for r in rows] == ["0:00–0:03", "0:03–0:11"]


def test_shot_list_validation_fails_closed():
    bad = [{"t": "0:00–0:03", "type": "A-roll", "shot": "x"}]  # missing overlay
    with pytest.raises(ProviderError):
        StubProvider().generate(_req("shot_list", slots={"beats": bad}))
    with pytest.raises(ProviderError):
        StubProvider().generate(_req("shot_list", slots={"beats": []}))


def test_thumbnail_composes_full_concept():
    out = StubProvider().generate(_req("thumbnail"))
    assert "REPLIES IN 60 SEC" in out and "Expression" in out


def test_caption_joins_title_angle_cta():
    out = StubProvider().generate(_req("caption"))
    assert out.splitlines() == ["Title line", "angle line", "cta line"]


def test_unknown_kind_rejected():
    with pytest.raises(ProviderError):
        StubProvider().generate(_req("not-a-kind"))


def test_render_template_never_keyerrors_on_missing_slot():
    out = render_template("Hello {topic} from {brand}", {"topic": "T"})
    assert out.startswith("Hello T from ")
