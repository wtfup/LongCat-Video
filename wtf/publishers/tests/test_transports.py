"""Transport + live-path tests.

Every live-path test runs through a recording FakeExecutor, so the full gated
live flow is exercised with ZERO real network (the autouse socket tripwire in
conftest asserts that on every single test).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from base import (
    CHANNELS,
    DryRunTransport,
    LiveTransport,
    PublishFailed,
    PublishPlan,
    PublishRefused,
    PublishStep,
    PublisherError,
    UnknownChannel,
    adapter_registry,
)
from executors import ExecutorCapabilityMissing, HttpResponse, StdlibHttpExecutor
from helpers import FakeExecutor, RecordingSleeper, env_for, make_request, write_approval

ALL_CHANNELS = pytest.mark.parametrize("channel", CHANNELS)


def responses_for(channel: str):
    if channel == "instagram":
        return [{"id": "container-123"}, {"status_code": "FINISHED"}, {"id": "ig-post-999"}]
    if channel == "youtube":
        return [
            HttpResponse(status_code=200, json={"id": "yt-session"}, headers={"Location": "https://upload.invalid/session/1"}),
            {"id": "yt-video-42"},
        ]
    if channel == "facebook":
        return [{"id": "fb-post-7"}]
    return [
        {"data": {"id": "media-777"}},
        {"data": {"id": "media-777"}},
        {"data": {"id": "media-777"}},
        {"data": {"id": "tweet-555"}},
    ]


def gate_open_env(channel: str, tmp_path) -> dict:
    write_approval(tmp_path, channel)
    return env_for(channel, PUBLISH_LIVE=channel)


def build_adapter(channel: str, tmp_path, *, live: bool = True, env=None, responses=None, sleeper=None, hook=None):
    cls = adapter_registry()[channel]
    fake = FakeExecutor(responses=responses if responses is not None else responses_for(channel), hook=hook)
    adapter = cls(
        dry_run=not live,
        approvals_dir=tmp_path,
        env=env,
        executor=fake,
        sleeper=sleeper or RecordingSleeper(),
    )
    return adapter, fake


# ---------------------------------------------------------------------------
# dry-run surface
# ---------------------------------------------------------------------------


def test_dry_run_transport_receipt_is_offline_and_deterministic(tmp_path):
    adapter = adapter_registry()["instagram"]()
    plan = adapter.plan(make_request("instagram", tmp_path))
    first = DryRunTransport().deliver(plan)
    second = DryRunTransport().deliver(plan)
    assert first.dry_run is True and first.accepted is False
    assert first.transport == "dry-run"
    assert first.provider_post_id.startswith("dry-run-")
    assert first.provider_post_id == second.provider_post_id
    assert first.steps_executed == 0
    assert first.steps_planned == len(plan.steps)
    assert "zero network" in first.notes[0]


def test_plan_digest_stable_and_content_bound(tmp_path):
    adapter = adapter_registry()["instagram"]()
    plan_a = adapter.plan(make_request("instagram", tmp_path, caption="A"))
    plan_b = adapter.plan(make_request("instagram", tmp_path, caption="A"))
    plan_c = adapter.plan(make_request("instagram", tmp_path, caption="B"))
    assert plan_a.digest() == plan_b.digest()
    assert plan_a.digest() != plan_c.digest()
    assert len(plan_a.digest()) == 64


@ALL_CHANNELS
def test_dry_run_default_receipt_reports_blocked_gate(channel, tmp_path):
    adapter, fake = build_adapter(channel, tmp_path, live=False, env={})
    receipt = adapter.publish(make_request(channel, tmp_path))
    assert receipt.dry_run is True
    assert receipt.transport == "dry-run"
    assert "env_unlock_missing" in receipt.gate_reason
    assert fake.calls == []


@ALL_CHANNELS
def test_dry_run_flag_wins_even_when_gate_open(channel, tmp_path):
    env = gate_open_env(channel, tmp_path)
    adapter, fake = build_adapter(channel, tmp_path, live=False, env=env)
    receipt = adapter.publish(make_request(channel, tmp_path))
    assert receipt.dry_run is True
    assert "gate_open_but_dry_run_default" in receipt.gate_reason
    assert fake.calls == []


# ---------------------------------------------------------------------------
# live refusals (gate closed => zero executor calls, ever)
# ---------------------------------------------------------------------------


@ALL_CHANNELS
def test_live_refused_with_no_keys(channel, tmp_path):
    adapter, fake = build_adapter(channel, tmp_path, env={})
    with pytest.raises(PublishRefused) as excinfo:
        adapter.publish(make_request(channel, tmp_path))
    assert "env_unlock_missing" in str(excinfo.value)
    assert fake.calls == []


@ALL_CHANNELS
def test_live_refused_with_env_only(channel, tmp_path):
    adapter, fake = build_adapter(channel, tmp_path, env={"PUBLISH_LIVE": channel})
    with pytest.raises(PublishRefused):
        adapter.publish(make_request(channel, tmp_path))
    assert fake.calls == []


@ALL_CHANNELS
def test_live_refused_with_approval_only(channel, tmp_path):
    write_approval(tmp_path, channel)
    adapter, fake = build_adapter(channel, tmp_path, env={})
    with pytest.raises(PublishRefused):
        adapter.publish(make_request(channel, tmp_path))
    assert fake.calls == []


def test_live_refused_with_expired_approval(tmp_path):
    write_approval(tmp_path, "instagram", expires_in_minutes=-5)
    env = env_for("instagram", PUBLISH_LIVE="instagram")
    adapter, fake = build_adapter("instagram", tmp_path, env=env)
    with pytest.raises(PublishRefused) as excinfo:
        adapter.publish(make_request("instagram", tmp_path))
    assert "approval_expired" in str(excinfo.value)
    assert fake.calls == []


def test_live_refused_when_approval_revoked_after_construction(tmp_path):
    """Constructed while valid, revoked before delivery => refusal, no call."""
    env = gate_open_env("instagram", tmp_path)
    adapter, fake = build_adapter("instagram", tmp_path, env=env)
    (tmp_path / "instagram.json").unlink()
    with pytest.raises(PublishRefused) as excinfo:
        adapter.publish(make_request("instagram", tmp_path))
    assert "approval_missing" in str(excinfo.value)
    assert fake.calls == []


def test_revocation_between_steps_stops_next_call(tmp_path):
    """Innermost re-check: delete the approval mid-flight; the NEXT provider
    call is refused even though the first one already executed."""
    env = gate_open_env("instagram", tmp_path)
    approval_path = tmp_path / "instagram.json"

    def revoke(kwargs, count):
        if count == 1:
            approval_path.unlink()

    adapter, fake = build_adapter("instagram", tmp_path, env=env, hook=revoke)
    with pytest.raises(PublishRefused):
        adapter.publish(make_request("instagram", tmp_path))
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# gated live success through a fake executor
# ---------------------------------------------------------------------------


@ALL_CHANNELS
def test_gated_live_success_with_fake_executor(channel, tmp_path):
    env = gate_open_env(channel, tmp_path)
    adapter, fake = build_adapter(channel, tmp_path, env=env)
    receipt = adapter.publish(make_request(channel, tmp_path))
    assert receipt.dry_run is False
    assert receipt.accepted is True
    assert receipt.transport == "live"
    assert receipt.steps_executed == receipt.steps_planned
    assert receipt.provider_post_id
    assert receipt.provider_post_id != "dry-run-" + receipt.payload_digest[:12]
    assert "ok" in receipt.gate_reason


def test_instagram_capture_threads_into_next_steps(tmp_path):
    env = gate_open_env("instagram", tmp_path)
    adapter, fake = build_adapter("instagram", tmp_path, env=env)
    receipt = adapter.publish(make_request("instagram", tmp_path))
    assert fake.calls[1]["url"].endswith("/container-123")
    assert fake.calls[1]["query"] == {"fields": "status_code,status"}
    assert fake.calls[2]["json_body"] == {"creation_id": "container-123"}
    assert receipt.provider_post_id == "ig-post-999"


def test_youtube_location_header_capture_feeds_upload(tmp_path):
    env = gate_open_env("youtube", tmp_path)
    adapter, fake = build_adapter("youtube", tmp_path, env=env)
    adapter.publish(make_request("youtube", tmp_path))
    assert fake.calls[1]["url"] == "https://upload.invalid/session/1"
    assert fake.calls[1]["capability"] == "binary_put"
    assert fake.calls[1]["binary_file"].endswith("render.mp4")
    assert fake.calls[1]["headers"]["Content-Type"] == "video/mp4"


def test_x_media_id_threads_into_tweet(tmp_path):
    env = gate_open_env("x", tmp_path)
    adapter, fake = build_adapter("x", tmp_path, env=env)
    adapter.publish(make_request("x", tmp_path))
    assert fake.calls[3]["json_body"]["media"]["media_ids"] == ["media-777"]
    assert fake.calls[0]["headers"]["Authorization"].startswith("OAuth ")


def test_facebook_bearer_auth_header(tmp_path):
    env = gate_open_env("facebook", tmp_path)
    adapter, fake = build_adapter("facebook", tmp_path, env=env)
    adapter.publish(make_request("facebook", tmp_path))
    assert fake.calls[0]["headers"]["Authorization"].startswith("Bearer ")


def test_credential_values_never_reach_the_receipt(tmp_path):
    env = gate_open_env("instagram", tmp_path)
    sentinel = "SENTINEL-CREDENTIAL-VALUE"
    env["IG_GRAPH_ACCESS_TOKEN"] = sentinel
    env["IG_USER_ID"] = "SENTINEL-USER-ID"
    adapter, fake = build_adapter("instagram", tmp_path, env=env)
    receipt = adapter.publish(make_request("instagram", tmp_path))
    blob = receipt.to_json()
    assert sentinel not in blob
    assert "SENTINEL-USER-ID" not in blob
    # ...but the executor did receive the auth header (wiring works).
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer " + sentinel


def test_x_oauth_header_never_leaks_secret_values(tmp_path):
    env = gate_open_env("x", tmp_path)
    env["X_CONSUMER_SECRET"] = "SENTINEL-CONSUMER-SECRET"
    env["X_ACCESS_TOKEN_SECRET"] = "SENTINEL-TOKEN-SECRET"
    adapter, fake = build_adapter("x", tmp_path, env=env)
    receipt = adapter.publish(make_request("x", tmp_path))
    auth = fake.calls[0]["headers"]["Authorization"]
    assert "SENTINEL-CONSUMER-SECRET" not in auth
    assert "SENTINEL-TOKEN-SECRET" not in auth
    assert "SENTINEL-CONSUMER-SECRET" not in receipt.to_json()


# ---------------------------------------------------------------------------
# failure modes
# ---------------------------------------------------------------------------


def test_missing_credentials_refused_before_any_call(tmp_path):
    env = gate_open_env("instagram", tmp_path)
    del env["IG_USER_ID"]
    adapter, fake = build_adapter("instagram", tmp_path, env=env)
    with pytest.raises(PublisherError) as excinfo:
        adapter.publish(make_request("instagram", tmp_path))
    message = str(excinfo.value)
    assert "IG_USER_ID" in message
    assert "TEST-PLACEHOLDER-IG_GRAPH_ACCESS_TOKEN" not in message
    assert fake.calls == []


def test_non_2xx_step_fails_closed(tmp_path):
    env = gate_open_env("facebook", tmp_path)
    responses = [HttpResponse(status_code=500, json={"error": "boom"})]
    adapter, fake = build_adapter("facebook", tmp_path, env=env, responses=responses)
    with pytest.raises(PublishFailed) as excinfo:
        adapter.publish(make_request("facebook", tmp_path))
    assert "HTTP 500" in str(excinfo.value)
    assert len(fake.calls) == 1


def test_missing_capture_fails_closed(tmp_path):
    env = gate_open_env("instagram", tmp_path)
    responses = [{}]
    adapter, fake = build_adapter("instagram", tmp_path, env=env, responses=responses)
    with pytest.raises(PublishFailed) as excinfo:
        adapter.publish(make_request("instagram", tmp_path))
    assert "capture" in str(excinfo.value)
    assert len(fake.calls) == 1


def test_poll_retries_then_succeeds(tmp_path):
    env = gate_open_env("instagram", tmp_path)
    sleeper = RecordingSleeper()
    responses = [
        {"id": "container-123"},
        {"status_code": "IN_PROGRESS"},
        {"status_code": "FINISHED"},
        {"id": "ig-post-999"},
    ]
    adapter, fake = build_adapter("instagram", tmp_path, env=env, responses=responses, sleeper=sleeper)
    receipt = adapter.publish(make_request("instagram", tmp_path))
    assert receipt.accepted is True
    assert receipt.steps_executed == 4
    assert sleeper.calls == [10.0]


def test_poll_timeout_fails_closed(tmp_path):
    env = gate_open_env("instagram", tmp_path)
    sleeper = RecordingSleeper()
    responses = [{"id": "container-123"}, {"status_code": "IN_PROGRESS"}, {"status_code": "IN_PROGRESS"}]
    adapter, fake = build_adapter("instagram", tmp_path, env=env, responses=responses, sleeper=sleeper)
    plan = adapter.plan(make_request("instagram", tmp_path))
    steps = list(plan.steps)
    steps[1] = replace(steps[1], poll={**dict(steps[1].poll), "max_attempts": 2})
    plan = replace(plan, steps=tuple(steps))
    transport = LiveTransport(
        executor=fake,
        sleeper=sleeper,
        approvals_dir=tmp_path,
        env=env,
        credential_spec=adapter.credential_spec,
        header_builder=adapter.build_auth_headers,
    )
    with pytest.raises(PublishFailed) as excinfo:
        transport.deliver(plan)
    assert "timed out" in str(excinfo.value)
    assert sleeper.calls == [10.0]


# ---------------------------------------------------------------------------
# stdlib executor hardening
# ---------------------------------------------------------------------------


def test_stdlib_executor_refuses_multipart_capability():
    executor = StdlibHttpExecutor(guard=lambda: None)
    with pytest.raises(ExecutorCapabilityMissing):
        executor(method="POST", url="https://api.invalid", capability="multipart")


def test_stdlib_executor_refuses_multipart_payload_under_json():
    executor = StdlibHttpExecutor(guard=lambda: None)
    with pytest.raises(ExecutorCapabilityMissing):
        executor(method="POST", url="https://api.invalid", multipart=({"name": "a", "value": "b"},), capability="json")


def test_stdlib_executor_refuses_without_guard():
    executor = StdlibHttpExecutor()
    with pytest.raises(RuntimeError):
        executor(method="GET", url="https://api.invalid", capability="json")


def test_stdlib_executor_guard_runs_before_network():
    def guard():
        raise PublishRefused("blocked by test guard")

    executor = StdlibHttpExecutor(guard=guard)
    with pytest.raises(PublishRefused):
        executor(method="GET", url="https://api.invalid", capability="json")


def test_stdlib_executor_is_the_injected_default_lazily():
    transport = LiveTransport(approvals_dir="/nonexistent", env={})
    resolved = transport._resolved_executor()
    assert isinstance(resolved, StdlibHttpExecutor)
    assert transport._resolved_executor() is resolved


def test_default_stdlib_executor_carries_the_gate(tmp_path):
    """The default executor's own guard refuses while the gate is closed."""
    transport = LiveTransport(approvals_dir=tmp_path, env={})
    transport._current_channel = "instagram"
    executor = transport._resolved_executor()
    with pytest.raises(PublishRefused):
        executor(method="GET", url="https://api.invalid/", capability="json")


def test_rfc3986_quote_matches_stdlib_quote():
    from urllib.parse import quote

    from x_api import rfc3986_quote

    samples = [
        "",
        "simple",
        "a b",
        "a+b",
        "x/y?z=1&k=2",
        "caf\u00e9",
        "na\u00efve \u2713",
        "~._-",
        "100%",
        'quote"mark',
        "line\nbreak",
    ]
    for sample in samples:
        assert rfc3986_quote(sample) == quote(sample, safe=""), sample


# ---------------------------------------------------------------------------
# plan validation + registry
# ---------------------------------------------------------------------------


def test_plan_validation_errors(tmp_path):
    ok_step = PublishStep(name="s", method="POST", url="https://graph.invalid/x")
    with pytest.raises(PublisherError):
        PublishPlan(channel="instagram", job_id="j", steps=()).validate()
    with pytest.raises(PublisherError):
        PublishPlan(channel="instagram", job_id="j", steps=(replace(ok_step, capability="carrier-pigeon"),)).validate()
    with pytest.raises(PublisherError):
        PublishPlan(channel="instagram", job_id="j", steps=(replace(ok_step, method="FETCH"),)).validate()
    with pytest.raises(PublisherError):
        PublishPlan(channel="instagram", job_id="j", steps=(replace(ok_step, poll={"field": "x"}),)).validate()
    with pytest.raises(UnknownChannel):
        PublishPlan(channel="tiktok", job_id="j", steps=(ok_step,)).validate()


def test_registry_covers_every_channel():
    registry = adapter_registry()
    assert set(registry) == set(CHANNELS)
    for channel, cls in registry.items():
        assert cls.channel == channel
        assert cls().dry_run is True


def test_plan_has_no_secret_material(tmp_path):
    for channel in CHANNELS:
        adapter = adapter_registry()[channel]()
        plan = adapter.plan(make_request(channel, tmp_path))
        blob = json.dumps({"steps": [step.url + str(step.query) + str(step.json_body) for step in plan.steps]})
        assert "SECRET" not in blob
        assert "TOKEN" not in blob
