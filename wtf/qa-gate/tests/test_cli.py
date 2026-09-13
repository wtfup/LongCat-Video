"""CLI contract: exit codes, JSON output, record + run subcommands."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "gate.py"


def _cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(GATE), *args],
        capture_output=True, text=True, cwd=str(cwd or GATE.parent),
    )


def test_cli_version():
    r = _cli("version")
    assert r.returncode == 0
    assert "wtf-qa-gate" in r.stdout


def test_cli_score_accept_writes_json(clips, tmp_path):
    out = tmp_path / "report.json"
    r = _cli("score", "--video", str(clips["good"]), "--json", str(out))
    assert r.returncode == 0, r.stderr
    assert "accept" in r.stdout.lower()
    rep = json.loads(out.read_text())
    assert rep["verdict"] == "accept"


def test_cli_score_reject_exit_code_2(clips, tmp_path):
    out = tmp_path / "report.json"
    r = _cli("score", "--video", str(clips["black"]), "--json", str(out))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "reject" in r.stdout.lower()
    assert json.loads(out.read_text())["verdict"] == "reject"


def test_cli_bestof_picks_winner(clips, tmp_path):
    out = tmp_path / "sel.json"
    r = _cli("bestof", "--candidates", f"{clips['black']},{clips['good']}",
             "--json", str(out))
    assert r.returncode == 0, r.stderr
    sel = json.loads(out.read_text())
    assert sel["winner"] == str(clips["good"])


def test_cli_bestof_no_winner_exit_2(clips, tmp_path):
    r = _cli("bestof", "--candidates", f"{clips['black']},{clips['silent']}")
    assert r.returncode == 2


def test_cli_record(make_db, tmp_path):
    db = make_db()
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"verdict": "accept", "score": 88.0,
                               "policy_id": "p", "codes": [], "reasons": []}))
    r = _cli("record", "--db", db, "--job", "job-1", "--report", str(rep))
    assert r.returncode == 0, r.stderr
    con = sqlite3.connect(db)
    row = con.execute("SELECT status, gate_score FROM jobs WHERE id='job-1'").fetchone()
    con.close()
    assert row == ("gated", 88.0)


def test_cli_run_end_to_end(clips, make_db):
    db = make_db(render_path=str(clips["good"]))
    r = _cli("run", "--db", db, "--job", "job-1")
    assert r.returncode == 0, r.stderr
    con = sqlite3.connect(db)
    row = con.execute("SELECT status, gate_score FROM jobs WHERE id='job-1'").fetchone()
    ev = con.execute("SELECT event FROM events WHERE job_id='job-1'").fetchall()
    con.close()
    assert row[0] == "gated"
    assert row[1] == 100.0
    assert ev == [("gate_passed",)]


def test_cli_selftest_reports_tools():
    r = _cli("selftest")
    assert r.returncode == 0, r.stderr
    assert "ffprobe" in r.stdout and "ffmpeg" in r.stdout
