"""CLI tests: the offline operator surface (in-process + one subprocess smoke)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import cli
from base import CHANNELS
from helpers import write_approval

PKG_DIR = Path(__file__).resolve().parents[1]


def run_main(argv):
    buffer = io.StringIO()
    code = cli.main(argv, stdout=buffer)
    return code, buffer.getvalue()


def test_gate_report_json_all_blocked(monkeypatch):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    code, output = run_main(["gate-report", "--json"])
    assert code == 0
    payload = json.loads(output)
    assert payload["env_unlock_var"] == "PUBLISH_LIVE"
    assert set(payload["decisions"]) == set(CHANNELS)
    assert all(not decision["live_allowed"] for decision in payload["decisions"].values())


def test_gate_report_table(monkeypatch):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    code, output = run_main(["gate-report"])
    assert code == 0
    assert "live_allowed: 0/4" in output
    for channel in CHANNELS:
        assert channel in output


def test_gate_report_with_single_channel_unlocked(tmp_path, monkeypatch):
    write_approval(tmp_path, "instagram")
    monkeypatch.setenv("PUBLISH_LIVE", "instagram")
    code, output = run_main(["gate-report", "--json", "--approvals-dir", str(tmp_path)])
    assert code == 0
    payload = json.loads(output)
    assert payload["decisions"]["instagram"]["live_allowed"] is True
    for other in ("youtube", "facebook", "x"):
        assert payload["decisions"][other]["live_allowed"] is False


def test_dry_run_renders_receipt(monkeypatch):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    code, output = run_main(
        [
            "dry-run",
            "--channel",
            "instagram",
            "--job-id",
            "job-1",
            "--title",
            "T",
            "--caption",
            "C",
            "--video",
            "/tmp/render.mp4",
        ]
    )
    assert code == 0
    receipt = json.loads(output)
    assert receipt["dry_run"] is True
    assert receipt["provider_post_id"].startswith("dry-run-")
    assert receipt["steps_planned"] == 3
    assert receipt["steps_executed"] == 0
    assert receipt["transport"] == "dry-run"


def test_dry_run_unknown_channel_is_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            ["dry-run", "--channel", "tiktok", "--job-id", "j", "--title", "t", "--video", "/tmp/x.mp4"],
            stdout=io.StringIO(),
        )
    assert excinfo.value.code == 2


def test_adapters_command_json():
    code, output = run_main(["adapters", "--json"])
    assert code == 0
    payload = json.loads(output)
    assert set(payload) == set(CHANNELS)
    for channel, info in payload.items():
        assert info["dry_run_default"] is True
        assert info["credential_env"], channel


def test_cli_subprocess_smoke():
    env = {key: value for key, value in os.environ.items() if key != "PUBLISH_LIVE"}
    proc = subprocess.run(
        [sys.executable, "cli.py", "gate-report", "--json"],
        capture_output=True,
        text=True,
        cwd=str(PKG_DIR),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert all(not decision["live_allowed"] for decision in payload["decisions"].values())
