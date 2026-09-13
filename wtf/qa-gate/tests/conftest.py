"""Shared fixtures for the W7 QA Gate test suite.

- Synthetic clips are generated with local ffmpeg (lavfi sources only — no network).
- helpers: `write_policy` (deep-merge overrides into the default policy) and
  `make_db` (factory DB with the canonical schema from BRIEF.md).
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

import gate  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

# Exact canonical schema (BRIEF.md shared interface contract, v1).
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  brand TEXT NOT NULL,
  pillar TEXT NOT NULL,
  language TEXT NOT NULL,
  title TEXT,
  script TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  render_path TEXT,
  gate_score REAL,
  gate_json TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  event TEXT NOT NULL,
  meta TEXT
);
"""


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _emit_scalar(v) -> str:
    """Emit a scalar both parsers accept (PyYAML and gate's stdlib fallback)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if s == "" or s.strip() != s or any(c in s for c in ":#{}[],&*!|>'\"%@`"):
        import json

        return json.dumps(s)
    return s


def _emit_policy(data: dict, indent: int = 0) -> str:
    """Minimal block-style YAML emitter for nested maps + scalars."""
    lines = []
    pad = "  " * indent
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.append(_emit_policy(v, indent + 1))
        else:
            lines.append(f"{pad}{k}: {_emit_scalar(v)}")
    return "\n".join(lines) + "\n"


def _run(cmd: list) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _ff(out: Path, *args: str) -> None:
    _run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args, str(out)])


@pytest.fixture(scope="session")
def clips(tmp_path_factory):
    """Synthetic good/bad clips — one ffmpeg pass per fixture, session-scoped."""
    if not FFMPEG or not FFPROBE:
        pytest.skip("ffmpeg/ffprobe not available on this host")
    d = tmp_path_factory.mktemp("clips")

    good = d / "good.mp4"
    _ff(
        good,
        "-f", "lavfi", "-i", "testsrc2=d=3:s=320x240:r=10",
        "-f", "lavfi", "-i", "sine=f=440:d=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
    )

    black = d / "black.mp4"
    _ff(
        black,
        "-f", "lavfi", "-i", "color=c=black:d=3:s=320x240:r=10",
        "-f", "lavfi", "-i", "sine=f=440:d=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
    )

    silent = d / "silent.mp4"
    _ff(
        silent,
        "-f", "lavfi", "-i", "testsrc2=d=3:s=320x240:r=10",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
    )

    partial = d / "partial_black.mp4"
    _ff(
        partial,
        "-f", "lavfi", "-i", "testsrc2=d=4.5:s=320x240:r=10",
        "-f", "lavfi", "-i", "sine=f=440:d=5",
        "-f", "lavfi", "-i", "color=c=black:d=0.5:s=320x240:r=10",
        "-filter_complex", "[0:v][2:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
    )

    short = d / "short.mp4"
    _ff(
        short,
        "-f", "lavfi", "-i", "testsrc2=d=0.4:s=320x240:r=10",
        "-f", "lavfi", "-i", "sine=f=440:d=0.4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
    )

    noaudio = d / "noaudio.mp4"
    _ff(
        noaudio,
        "-f", "lavfi", "-i", "testsrc2=d=3:s=320x240:r=10",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    )

    corrupt = d / "corrupt.mp4"
    corrupt.write_text("this is not a video file\n")

    good2 = d / "good_twin.mp4"
    shutil.copyfile(good, good2)

    return {
        "dir": d,
        "good": good,
        "good2": good2,
        "black": black,
        "silent": silent,
        "partial": partial,
        "short": short,
        "noaudio": noaudio,
        "corrupt": corrupt,
    }


@pytest.fixture
def write_policy(tmp_path):
    """Write a policy file derived from the packaged default + deep overrides.

    Uses the test-local emitter (not PyYAML) so the suite runs on stdlib+pytest
    alone — e.g. `uv run --no-project --with pytest python -m pytest`.
    """
    def _write(**overrides):
        data = _deep_merge(gate.Policy.load().data, overrides)
        p = tmp_path / "policy_override.yaml"
        p.write_text(_emit_policy(data))
        return str(p)

    return _write


@pytest.fixture
def make_policy_file(tmp_path):
    """Write a policy file from a dict (emitted) or raw text (verbatim)."""
    def _make(data=None, text=None, name="policy_custom.yaml"):
        p = tmp_path / name
        if text is not None:
            p.write_text(text)
        else:
            p.write_text(_emit_policy(data))
        return str(p)

    return _make


@pytest.fixture
def make_db(tmp_path):
    """Create a factory.db with the canonical schema + one job row."""
    def _make(job_id="job-1", status="rendered", render_path=None, filename="factory.db"):
        path = tmp_path / filename
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO jobs (id, created_at, brand, pillar, language, status, render_path) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, "2026-09-13T00:00:00Z", "WTF", "AI systems", "en", status, render_path),
        )
        con.commit()
        con.close()
        return str(path)

    return _make


def read_job(db_path, job_id):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    events = con.execute(
        "SELECT ts, event, meta FROM events WHERE job_id=? ORDER BY id", (job_id,)
    ).fetchall()
    con.close()
    return row, events
