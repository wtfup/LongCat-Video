#!/usr/bin/env python
"""W2 console — end-to-end evidence run against an isolated demo DB.

Exercises the full approval journey through the real FastAPI app via
TestClient (no server socket, no network):

  1. health check
  2. queue two jobs (hi / en)
  3. simulate W3 render + W7 gate writes -> 'gated' with gate receipt
  4. gated queue listing + job detail (render + gate visible)
  5. approve one job, reject the other with a reason
  6. verify DB rows + 'status_change' audit events (W1 vocabulary)
  7. idempotent replay + invalid transition blocked (409)
  8. guarded render serving + traversal refusal (403)
  9. zero-network proof: every socket operation blocked, cycle still works

Prints a transcript with PASS/FAIL lines. Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG))

from fastapi.testclient import TestClient  # noqa: E402

import db as factory_db  # noqa: E402
from app import create_app  # noqa: E402

DEMO = HERE / "demo"
FAILURES: list = []


def check(label: str, condition: bool, extra: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f"  ({extra})" if extra else ""))
    if not condition:
        FAILURES.append(label)


def main() -> int:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    media = DEMO / "renders"
    media.mkdir(parents=True)
    db_path = DEMO / "factory-demo.db"

    print("== W2 console — end-to-end evidence ==")
    print(f"demo db    : {db_path}")
    print(f"media root : {media}")
    print()

    client = TestClient(create_app(db_path=str(db_path), media_root=str(media)))

    # 1 ---------------------------------------------------------------- health
    r = client.get("/health")
    check("1 health ok", r.status_code == 200 and r.json().get("db_ok") is True,
          f"status={r.json().get('status')} jobs_total={r.json().get('jobs_total')}")

    # 2 ------------------------------------------------------------ queue jobs
    r1 = client.post("/jobs", json={
        "brand": "WTF Gyms", "pillar": "AI systems", "language": "hi",
        "title": "Hindi pilot cut", "script": "0-3s: hook / 3-20s: proof / 20-45s: CTA",
    })
    r2 = client.post("/jobs", json={
        "brand": "EVRYDAY", "pillar": "fitness/transformation", "language": "en",
        "title": "EVRYDAY launch", "script": "0-3s: hook / 3-20s: story / 20-45s: CTA",
    })
    check("2 queue job (hi)", r1.status_code == 201, r1.json().get("id", ""))
    check("2 queue job (en)", r2.status_code == 201, r2.json().get("id", ""))
    j1, j2 = r1.json()["id"], r2.json()["id"]

    # 3 --------------------------------------- simulate render + QA gate writes
    clip1 = media / f"{j1}.mp4"
    clip1.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
    clip2 = media / f"{j2}.mp4"
    clip2.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048)
    con = factory_db.connect(str(db_path))
    for jid, score in ((j1, 0.91), (j2, 0.64)):
        con.execute(
            "UPDATE jobs SET status='gated', render_path=?, gate_score=?, gate_json=? WHERE id=?",
            (
                str(media / f"{jid}.mp4"),
                score,
                json.dumps({"passed": score >= 0.7, "lip_sync_wer": 0.12, "black_frames": 0}),
                jid,
            ),
        )
        con.execute(
            "INSERT INTO events (job_id, ts, event, meta) VALUES (?, ?, ?, ?)",
            (jid, factory_db.utc_now(), "status_change",
             json.dumps({"from": "rendered", "to": "gated", "by": "qa-gate"})),
        )
    con.execute("COMMIT")
    con.close()

    # 4 ------------------------------------------------------- list + detail
    r = client.get("/jobs", params={"status": "gated"})
    check("4 gated queue lists both", r.status_code == 200 and r.json()["count"] == 2,
          f"count={r.json().get('count')}")

    d = client.get(f"/jobs/{j1}").json()
    check("4 detail: render available", d.get("render_available") is True, d.get("render_url") or "")
    check("4 detail: gate receipt visible", d.get("gate_score") == 0.91 and d.get("gate_json") is not None)
    check("4 detail: audit events visible",
          any(e["event"] == "status_change" for e in d.get("events", [])))

    # 5 -------------------------------------------------------------- decisions
    a = client.post(f"/jobs/{j1}/approve")
    check("5 approve job 1", a.status_code == 200 and a.json().get("changed") is True,
          json.dumps(a.json()))
    rj = client.post(f"/jobs/{j2}/reject", json={"reason": "lighting flicker at 0:07"})
    check("5 reject job 2 with reason",
          rj.status_code == 200 and rj.json().get("status") == "rejected", json.dumps(rj.json()))

    # 6 -------------------------------------------------------------- db verify
    con = factory_db.connect(str(db_path))
    s1 = con.execute("SELECT status FROM jobs WHERE id=?", (j1,)).fetchone()["status"]
    s2 = con.execute("SELECT status FROM jobs WHERE id=?", (j2,)).fetchone()["status"]
    notes = con.execute("SELECT notes FROM jobs WHERE id=?", (j2,)).fetchone()["notes"]
    ev = con.execute("SELECT event, meta FROM events WHERE job_id=? ORDER BY id", (j1,)).fetchall()
    con.close()
    check("6 db: job 1 status=approved", s1 == "approved", s1)
    check("6 db: job 2 status=rejected", s2 == "rejected", s2)
    check("6 db: reject reason persisted to notes", "lighting flicker" in (notes or ""))
    check("6 db: status_change event with from/to/by",
          ev[-1]["event"] == "status_change"
          and json.loads(ev[-1]["meta"])["to"] == "approved"
          and json.loads(ev[-1]["meta"])["by"] == "console")

    # 7 ------------------------------------------- idempotency + invalid gate
    a2 = client.post(f"/jobs/{j1}/approve")
    check("7 idempotent approve replay", a2.status_code == 200 and a2.json().get("changed") is False)

    j3r = client.post("/jobs", json={
        "brand": "Reboot", "pillar": "business", "language": "hinglish", "title": "Queued only",
    })
    j3 = j3r.json()["id"]
    bad = client.post(f"/jobs/{j3}/approve")
    check("7 invalid transition blocked (409 queued->approved)",
          bad.status_code == 409 and bad.json()["detail"]["error"] == "invalid_transition",
          json.dumps(bad.json().get("detail", {})))

    # 8 -------------------------------------------------------- render serving
    rr = client.get(f"/jobs/{j1}/render")
    check("8 render served (video/mp4)",
          rr.status_code == 200 and rr.headers["content-type"].startswith("video/mp4"),
          f"{len(rr.content)} bytes")

    outside = DEMO / "outside-renders" / "evil.mp4"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"outside-media-root")
    con = factory_db.connect(str(db_path))
    con.execute("UPDATE jobs SET render_path=? WHERE id=?", (str(outside), j3))
    con.execute("COMMIT")
    con.close()
    trav = client.get(f"/jobs/{j3}/render")
    check("8 traversal outside media root refused (403)",
          trav.status_code == 403 and trav.json()["detail"]["reason"] == "outside_media_root")

    # 9 --------------------------------------------------------- zero network
    patched = []
    for obj, attr in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                      (socket, "create_connection"), (socket, "getaddrinfo")):
        patched.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("network call attempted")))
    try:
        network_ok = client.get("/health").status_code == 200
        network_ok = network_ok and client.get("/jobs").status_code == 200
    finally:
        for obj, attr, original in patched:
            setattr(obj, attr, original)
    check("9 zero network calls (all sockets blocked)", network_ok)

    print()
    print(f"FAILURES: {len(FAILURES)}")
    for label in FAILURES:
        print(f"  - {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
