"""W2 console test suite (fastapi TestClient, isolated temp DBs).

Every test runs fully offline. ``test_zero_network_calls`` additionally blocks
socket operations during a complete request cycle to prove the console makes
zero network calls.

Run from the package dir:  python -m pytest
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PKG_DIR = Path(__file__).resolve().parents[1]
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

import db as factory_db  # noqa: E402
from app import create_app  # noqa: E402

VALID_BODY = {
    "brand": "WTF Gyms",
    "pillar": "AI systems",
    "language": "en",
    "title": "Console smoke",
    "script": "0-3s hook / 3-20s body / 20-45s CTA",
}


@pytest.fixture()
def env(tmp_path):
    db_path = tmp_path / "factory.db"
    media_root = tmp_path / "renders"
    media_root.mkdir()
    client = TestClient(create_app(db_path=str(db_path), media_root=str(media_root)))
    return {
        "client": client,
        "db": str(db_path),
        "media": media_root,
        "tmp": tmp_path,
    }


def seed(env, **kwargs):
    """Insert a job directly at whatever lifecycle stage a test needs."""
    payload = {
        "brand": kwargs.pop("brand", "WTF Gyms"),
        "pillar": kwargs.pop("pillar", "AI systems"),
        "language": kwargs.pop("language", "en"),
        "title": kwargs.pop("title", "Seed job"),
        "script": kwargs.pop("script", "seed script"),
    }
    payload.update(kwargs)
    return factory_db.create_job(env["db"], **payload)


def _fake_mp4(path: Path) -> bytes:
    payload = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    path.write_bytes(payload)
    return payload


# ---------------------------------------------------------------- health


def test_health_ok(env):
    r = env["client"].get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db_ok"] is True
    assert body["service"] == "wtf-avatar-console"
    assert body["jobs_total"] == 0
    assert body["db_path"] == env["db"]


def test_health_degraded_when_db_unusable(tmp_path):
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory", encoding="utf-8")
    client = TestClient(
        create_app(db_path=str(blocker / "factory.db"), media_root=str(tmp_path))
    )
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["db_ok"] is False
    assert client.get("/jobs").status_code == 503


# ---------------------------------------------------------------- create


def test_create_job_defaults_and_created_event(env):
    r = env["client"].post("/jobs", json=VALID_BODY)
    assert r.status_code == 201
    job = r.json()
    assert job["id"].startswith("job_")
    assert job["status"] == "queued"
    assert job["brand"] == "WTF Gyms"
    assert job["pillar"] == "AI systems"
    assert job["created_at"]
    assert job["render_path"] is None
    assert job["gate_score"] is None

    con = factory_db.connect(env["db"])
    try:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["status"] == "queued"
        events = con.execute(
            "SELECT event FROM events WHERE job_id=? ORDER BY id", (job["id"],)
        ).fetchall()
        assert [e["event"] for e in events] == ["created"]
    finally:
        con.close()


def test_create_job_custom_id_and_duplicate(env):
    body = dict(VALID_BODY, id="job_20260913_demo01")
    assert env["client"].post("/jobs", json=body).status_code == 201
    dup = env["client"].post("/jobs", json=body)
    assert dup.status_code == 409
    assert dup.json()["detail"]["error"] == "duplicate_id"


def test_create_job_validation(env):
    c = env["client"]
    assert c.post("/jobs", json={"brand": "x", "pillar": "y"}).status_code == 422
    assert c.post("/jobs", json=dict(VALID_BODY, brand="   ")).status_code == 422
    assert c.post("/jobs", json=dict(VALID_BODY, brand="b" * 121)).status_code == 422
    assert c.post("/jobs", json=dict(VALID_BODY, language="E N")).status_code == 422
    assert c.post("/jobs", json=dict(VALID_BODY, id="x")).status_code == 422
    assert c.post("/jobs", json=dict(VALID_BODY, title="t" * 301)).status_code == 422
    assert c.post("/jobs", json=dict(VALID_BODY, script="s" * 20001)).status_code == 422


def test_language_normalised(env):
    r = env["client"].post("/jobs", json=dict(VALID_BODY, language="HI"))
    assert r.status_code == 201
    assert r.json()["language"] == "hi"


# ---------------------------------------------------------------- list


def test_list_jobs_filter_order_paging(env):
    seed(env, job_id="job_a", brand="EVRYDAY", status="queued",
         created_at="2026-09-01T00:00:00+00:00")
    seed(env, job_id="job_b", status="gated",
         created_at="2026-09-02T00:00:00+00:00")
    seed(env, job_id="job_c", status="gated",
         created_at="2026-09-03T00:00:00+00:00")
    c = env["client"]

    everything = c.get("/jobs").json()
    assert everything["count"] == 3
    assert [j["id"] for j in everything["jobs"]] == ["job_c", "job_b", "job_a"]

    gated = c.get("/jobs", params={"status": "gated"}).json()
    assert [j["id"] for j in gated["jobs"]] == ["job_c", "job_b"]

    by_brand = c.get("/jobs", params={"brand": "EVRYDAY"}).json()
    assert [j["id"] for j in by_brand["jobs"]] == ["job_a"]

    paged = c.get("/jobs", params={"limit": 1, "offset": 1}).json()
    assert [j["id"] for j in paged["jobs"]] == ["job_b"]

    assert c.get("/jobs", params={"status": "nope"}).status_code == 422
    assert c.get("/jobs", params={"limit": 0}).status_code == 422
    assert c.get("/jobs", params={"limit": 501}).status_code == 422


# ---------------------------------------------------------------- detail


def test_get_job_detail_and_404(env):
    seed(
        env,
        job_id="job_g1",
        status="gated",
        gate_score=0.87,
        gate_json=json.dumps({"passed": True, "lip_sync_wer": 0.11}),
    )
    r = env["client"].get("/jobs/job_g1")
    assert r.status_code == 200
    body = r.json()
    assert body["gate_score"] == 0.87
    assert json.loads(body["gate_json"])["passed"] is True
    assert [e["event"] for e in body["events"]] == ["created"]
    assert body["render_available"] is False
    assert env["client"].get("/jobs/does-not-exist").status_code == 404


# ---------------------------------------------------------------- decisions


def test_approve_flow_and_idempotent_replay(env):
    seed(env, job_id="job_ap", status="gated", gate_score=0.91)
    c = env["client"]
    r = c.post("/jobs/job_ap/approve")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "job_ap", "status": "approved", "changed": True}
    assert factory_db.get_job(env["db"], "job_ap")["status"] == "approved"

    again = c.post("/jobs/job_ap/approve")
    assert again.status_code == 200
    assert again.json()["changed"] is False
    assert factory_db.get_job(env["db"], "job_ap")["status"] == "approved"

    events = factory_db.get_events(env["db"], "job_ap")
    assert [e["event"] for e in events] == ["created", "status_change"]
    meta = json.loads(events[-1]["meta"])
    assert meta["from"] == "gated"
    assert meta["to"] == "approved"
    assert meta["by"] == "console"


def test_reject_flow_with_reason(env):
    seed(env, job_id="job_rj", status="gated")
    c = env["client"]
    r = c.post("/jobs/job_rj/reject", json={"reason": "lip-sync drifts at 0:12"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["changed"] is True

    job = factory_db.get_job(env["db"], "job_rj")
    assert job["status"] == "rejected"
    assert "lip-sync drifts" in job["notes"]

    events = factory_db.get_events(env["db"], "job_rj")
    meta = json.loads(events[-1]["meta"])
    assert meta["reason"] == "lip-sync drifts at 0:12"

    again = c.post("/jobs/job_rj/reject")
    assert again.status_code == 200
    assert again.json()["changed"] is False


def test_reject_without_body(env):
    seed(env, job_id="job_nb", status="gated")
    r = env["client"].post("/jobs/job_nb/reject")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


def test_decisions_blocked_outside_gated(env):
    seed(env, job_id="job_q", status="queued")
    seed(env, job_id="job_r", status="rejected")
    seed(env, job_id="job_p", status="published")
    c = env["client"]

    r = c.post("/jobs/job_q/approve")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "invalid_transition"
    assert r.json()["detail"]["current"] == "queued"
    assert r.json()["detail"]["allowed_from"] == ["gated"]

    assert c.post("/jobs/job_q/reject").status_code == 409
    assert c.post("/jobs/job_r/approve").status_code == 409
    assert c.post("/jobs/job_p/reject").status_code == 409
    assert factory_db.get_job(env["db"], "job_q")["status"] == "queued"


def test_decision_unknown_job_404(env):
    c = env["client"]
    assert c.post("/jobs/nope/approve").status_code == 404
    assert c.post("/jobs/nope/reject").status_code == 404
    assert c.post("/jobs/nope/approve").json()["detail"]["error"] == "not_found"


def test_reject_reason_too_long(env):
    seed(env, job_id="job_rl", status="gated")
    r = env["client"].post("/jobs/job_rl/reject", json={"reason": "x" * 501})
    assert r.status_code == 422


# ---------------------------------------------------------------- render


def test_render_served_inside_media_root(env):
    payload = _fake_mp4(env["media"] / "clip.mp4")
    seed(env, job_id="job_v", status="gated", render_path=str(env["media"] / "clip.mp4"))
    c = env["client"]
    r = c.get("/jobs/job_v/render")
    assert r.status_code == 200
    assert r.content == payload
    assert r.headers["content-type"].startswith("video/mp4")

    detail = c.get("/jobs/job_v").json()
    assert detail["render_available"] is True
    assert detail["render_url"] == "/jobs/job_v/render"


def test_render_refuses_outside_media_root(env):
    evil = env["tmp"] / "evil.mp4"
    _fake_mp4(evil)
    seed(env, job_id="job_e1", status="gated", render_path=str(evil))
    seed(env, job_id="job_e2", status="gated",
         render_path=str(env["media"] / ".." / "evil.mp4"))
    c = env["client"]
    r1 = c.get("/jobs/job_e1/render")
    r2 = c.get("/jobs/job_e2/render")
    assert r1.status_code == 403
    assert r1.json()["detail"]["reason"] == "outside_media_root"
    assert r2.status_code == 403


def test_render_refuses_symlink_escape(env):
    evil = env["tmp"] / "hidden.mp4"
    _fake_mp4(evil)
    link = env["media"] / "link.mp4"
    try:
        link.symlink_to(evil)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    seed(env, job_id="job_sl", status="gated", render_path=str(link))
    assert env["client"].get("/jobs/job_sl/render").status_code == 403


def test_render_missing_remote_absent(env):
    seed(env, job_id="job_m1", status="gated",
         render_path=str(env["media"] / "gone.mp4"))
    seed(env, job_id="job_m2", status="gated",
         render_path="https://example.invalid/x.mp4")
    seed(env, job_id="job_m3", status="gated")
    c = env["client"]
    assert c.get("/jobs/job_m1/render").status_code == 404
    r2 = c.get("/jobs/job_m2/render")
    assert r2.status_code == 403
    assert r2.json()["detail"]["reason"] == "remote_reference"
    assert c.get("/jobs/job_m3/render").status_code == 404


def test_render_unresolvable_path_fails_closed(env):
    """Adversarial regression: ENAMETOOLONG/null-byte paths must fail closed,
    not bubble an OSError out of the detail endpoint as a 503."""
    seed(env, job_id="job_long", status="gated", render_path="x" * 300)
    c = env["client"]
    detail = c.get("/jobs/job_long")
    assert detail.status_code == 200
    assert detail.json()["render_available"] is False
    assert detail.json()["render_note"] in ("unresolvable_path", "outside_media_root")
    assert c.get("/jobs/job_long/render").status_code in (403, 404)


# ---------------------------------------------------------------- spa


def test_static_spa_served(env):
    c = env["client"]
    index = c.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "Avatar Factory" in index.text

    css = c.get("/static/styles.css")
    assert css.status_code == 200
    assert "#8B0000" in css.text
    assert "#C9A227" in css.text

    js = c.get("/static/app.js")
    assert js.status_code == 200
    assert "approve" in js.text


def test_static_has_no_external_references():
    for name in ("index.html", "styles.css", "app.js"):
        text = (PKG_DIR / "static" / name).read_text(encoding="utf-8")
        for needle in ("http://", "https://", "//cdn.", "fonts.googleapis", "integrity="):
            assert needle not in text, f"external reference {needle!r} in {name}"


# ---------------------------------------------------------------- network


def test_zero_network_calls(env, monkeypatch):
    """Block every socket operation; a full request cycle must still succeed."""

    def _blocked(*args, **kwargs):
        raise AssertionError("console attempted a network call")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)

    c = env["client"]
    render_file = env["media"] / "n.mp4"
    _fake_mp4(render_file)
    seed(env, job_id="job_net", status="gated", render_path=str(render_file))
    assert c.get("/health").status_code == 200
    assert c.get("/jobs").status_code == 200
    assert c.post("/jobs", json=VALID_BODY).status_code == 201
    detail = c.get("/jobs/job_net")
    assert detail.status_code == 200, detail.text
    assert c.post("/jobs/job_net/approve").status_code == 200
    assert c.get("/jobs/job_net/render").status_code == 200
    assert c.get("/").status_code == 200


# ---------------------------------------------------------------- schema


def test_schema_matches_contract(env):
    con = factory_db.connect(env["db"])
    try:
        jobs_cols = tuple(r["name"] for r in con.execute("PRAGMA table_info(jobs)"))
        events_cols = tuple(r["name"] for r in con.execute("PRAGMA table_info(events)"))
    finally:
        con.close()
    assert jobs_cols == factory_db.EXPECTED_JOBS_COLUMNS
    assert events_cols == factory_db.EXPECTED_EVENTS_COLUMNS


def test_schema_init_idempotent(env):
    for _ in range(3):
        factory_db.connect(env["db"]).close()
    assert factory_db.health(env["db"])["jobs_total"] == 0


# ---------------------------------------------------------------- env


def test_env_var_override(monkeypatch, tmp_path):
    target = tmp_path / "from_env.db"
    monkeypatch.setenv("WTF_FACTORY_DB", str(target))
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["db_path"] == str(target)
