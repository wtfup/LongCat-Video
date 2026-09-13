"""Per-adapter plan-shape tests: exact endpoints, payloads, caps, and errors.

These lock the deploy contract: what each adapter would send, to where, with
which capabilities — all plan-only, zero network.
"""

from __future__ import annotations

import pytest

from base import CHANNELS, CredentialMissing, PublisherError, UnknownChannel, adapter_registry
from helpers import make_request

HOST_ALLOW = {
    "instagram": "https://graph.facebook.com/",
    "youtube": "https://www.googleapis.com/",
    "facebook": "https://graph.facebook.com/",
    "x": "https://api.x.com/",
}


@pytest.fixture
def plan_for(tmp_path):
    def _plan(channel, **overrides):
        adapter = adapter_registry()[channel]()
        return adapter.plan(make_request(channel, tmp_path, **overrides))

    return _plan


def test_all_adapters_default_to_dark_and_declare_credentials():
    for channel in CHANNELS:
        cls = adapter_registry()[channel]
        adapter = cls()
        assert adapter.channel == channel
        assert adapter.dry_run is True
        assert cls.credential_spec, channel
        for name in cls.credential_spec:
            assert name.isupper()


def test_all_plan_urls_use_expected_hosts(plan_for):
    for channel in CHANNELS:
        plan = plan_for(channel)
        for step in plan.steps:
            if step.url.startswith("${"):
                continue  # captured URL from an earlier step (e.g. resumable session)
            assert step.url.startswith(HOST_ALLOW[channel]), (channel, step.url)


def test_plan_step_names_are_unique(plan_for):
    for channel in CHANNELS:
        names = [step.name for step in plan_for(channel).steps]
        assert len(names) == len(set(names)), channel


# ---------------------------------------------------------------------------
# instagram
# ---------------------------------------------------------------------------


def test_instagram_plan_shape(plan_for):
    plan = plan_for("instagram")
    assert plan.summary["surface"] == "instagram_reels"
    assert [step.name for step in plan.steps] == ["create_container", "poll_container", "publish_container"]
    create, poll, publish = plan.steps
    assert create.url.endswith("/${IG_USER_ID}/media")
    assert create.json_body["media_type"] == "REELS"
    assert create.capture == {"container_id": "id"}
    assert poll.poll["until"] == "FINISHED"
    assert poll.query == {"fields": "status_code,status"}
    assert publish.url.endswith("/${IG_USER_ID}/media_publish")
    assert publish.json_body == {"creation_id": "${container_id}"}


def test_instagram_video_url_override_and_hashtags(plan_for):
    plan = plan_for(
        "instagram",
        caption="Hello world",
        hashtags=("AI", "WTF"),
        extra={"video_url": "https://cdn.wtfgyms.com/renders/x.mp4"},
    )
    body = plan.steps[0].json_body
    assert body["video_url"] == "https://cdn.wtfgyms.com/renders/x.mp4"
    assert "#AI #WTF" in body["caption"]
    assert plan.summary["hosting"] == "video_url"


def test_instagram_caption_is_capped(plan_for):
    plan = plan_for("instagram", caption="x" * 5000)
    assert len(plan.steps[0].json_body["caption"]) <= 2200


def test_instagram_rejects_non_public_visibility(plan_for):
    with pytest.raises(PublisherError):
        plan_for("instagram", visibility="private")


# ---------------------------------------------------------------------------
# youtube
# ---------------------------------------------------------------------------


def test_youtube_plan_shape(plan_for):
    plan = plan_for("youtube")
    assert plan.summary["surface"] == "youtube_shorts"
    init, upload = plan.steps
    assert init.url == "https://www.googleapis.com/upload/youtube/v3/videos"
    assert init.query == {"uploadType": "resumable", "part": "snippet,status"}
    assert init.capture == {"upload_url": "header:Location"}
    assert upload.capability == "binary_put"
    assert upload.url == "${upload_url}"
    assert upload.headers["Content-Type"] == "video/mp4"


def test_youtube_shorts_title_and_privacy_mapping(plan_for):
    plan = plan_for("youtube", title="My Short", visibility="unlisted")
    assert plan.steps[0].json_body["snippet"]["title"] == "My Short #Shorts"
    assert plan.steps[0].json_body["status"]["privacyStatus"] == "unlisted"
    assert plan.steps[0].json_body["snippet"]["channelId"] == "${YT_CHANNEL_ID}"
    long_title = plan_for("youtube", title="t" * 200)
    assert len(long_title.steps[0].json_body["snippet"]["title"]) <= 100
    assert long_title.steps[0].json_body["snippet"]["title"].endswith("#Shorts")


# ---------------------------------------------------------------------------
# facebook
# ---------------------------------------------------------------------------


def test_facebook_plan_shape_and_visibility_mapping(plan_for):
    public = plan_for("facebook")
    assert public.summary["surface"] == "facebook_page_video"
    (step,) = public.steps
    assert step.url.endswith("/${FB_PAGE_ID}/videos")
    assert step.json_body["published"] is True
    assert step.capture == {"post_id": "id"}
    draft = plan_for("facebook", visibility="private")
    assert draft.steps[0].json_body["published"] is False


# ---------------------------------------------------------------------------
# x
# ---------------------------------------------------------------------------


def test_x_plan_shape(plan_for):
    plan = plan_for("x")
    assert plan.summary["surface"] == "x_post"
    names = [step.name for step in plan.steps]
    assert names == ["init_upload", "append_upload", "finalize_upload", "create_post"]
    assert [step.capability for step in plan.steps] == ["multipart", "multipart", "multipart", "json"]
    assert plan.steps[3].json_body["media"]["media_ids"] == ["${media_id}"]
    assert plan.steps[0].capture == {"media_id": "data.id"}


def test_x_text_is_capped(plan_for):
    plan = plan_for("x", title="t" * 100, caption="c" * 5000)
    assert len(plan.steps[3].json_body["text"]) <= 280


def test_x_content_range_when_file_exists(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 10)
    adapter = adapter_registry()["x"]()
    plan = adapter.plan(make_request("x", tmp_path, video_path=str(video)))
    assert plan.steps[1].headers["Content-Range"] == "bytes 0-9/10"
    assert plan.summary["video_bytes"] == 10


def test_x_rejects_non_public_visibility(plan_for):
    with pytest.raises(PublisherError):
        plan_for("x", visibility="unlisted")


# ---------------------------------------------------------------------------
# shared request / auth behavior
# ---------------------------------------------------------------------------


def test_adapter_rejects_foreign_channel_request(tmp_path):
    adapter = adapter_registry()["instagram"]()
    with pytest.raises(UnknownChannel):
        adapter.publish(make_request("youtube", tmp_path))


def test_request_validation_errors(tmp_path):
    with pytest.raises(UnknownChannel):
        make_request("tiktok", tmp_path).validate()
    with pytest.raises(PublisherError):
        make_request("instagram", tmp_path, job_id="   ").validate()
    with pytest.raises(PublisherError):
        make_request("instagram", tmp_path, visibility="friends-only").validate()


def test_bearer_adapters_require_token_for_auth_header(tmp_path):
    for channel in ("instagram", "youtube", "facebook"):
        adapter = adapter_registry()[channel]()
        step = adapter.plan(make_request(channel, tmp_path)).steps[0]
        with pytest.raises(CredentialMissing):
            adapter.build_auth_headers({}, step, url=step.url, query={}, form_fields={})
        token_env = adapter.auth_token_env
        headers = adapter.build_auth_headers(
            {token_env: "TEST-PLACEHOLDER"}, step, url=step.url, query={}, form_fields={}
        )
        assert headers["Authorization"] == "Bearer TEST-PLACEHOLDER"
