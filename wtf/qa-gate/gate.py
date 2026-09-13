#!/usr/bin/env python3
"""WTF QA Gate v1 — realism/quality scoring for Avatar Factory renders.

Position in the pipeline (see PLAN.md):
    render (best-of-2)  ->  QA gate  ->  Console approval  ->  publish queue

What the gate does (all LOCAL — ffprobe/ffmpeg subprocesses only, zero network):

  1. integrity     — ffprobe parse, video/audio streams, duration bounds,
                     expected-duration fit, full decode-error scan
  2. black_frames  — blackdetect ratio against the policy ceiling
  3. silence       — silencedetect ratio + volumedetect mean level floor
  4. loudness      — mean dBFS floor (dead-audio detection)
  5. lip_sync_wer  — IFACE stubbed in v1 (faster-whisper scorer plugs in live)
  6. face_sim      — IFACE stubbed in v1 (identity similarity plugs in live)

Dark/stub defaults everywhere: the stubbed IFACEs report ``unavailable`` and are
excluded from the score (never fabricated). Best-of-N selects the highest-scoring
ELIGIBLE candidate (verdict == accept); if none qualifies it fails closed.

Job recording writes ``gate_score`` / ``gate_json`` and flips status per the
shared schema v1: accept -> ``gated`` (event ``gate_passed``),
reject -> ``rejected`` (event ``gate_rejected``).

CLI (exit codes: 0=accept, 2=reject, 1=error):
    python gate.py score  --video render.mp4 [--expect-duration 45] [--json out.json]
    python gate.py bestof --candidates a.mp4,b.mp4 [--json sel.json]
    python gate.py record --db factory.db --job <id> --report report.json [--init-db]
    python gate.py run    --db factory.db --job <id>
    python gate.py selftest | version

Self-audit: see README.md ``## 5. Self-audit``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

__version__ = "1.0.0"
GATE_ID = "wtf-qa-gate-v1"

_PKG_DIR = Path(__file__).resolve().parent
DEFAULT_POLICY_PATH = str(_PKG_DIR / "policy.yaml")
DEFAULT_DB_PATH = "/Users/vishalnigammacminioffice/wtf-avatar-factory/wtf/_data/factory.db"

ALLOWED_RECORD_STATES = ("rendered", "gated", "rejected")
CHECK_IDS = ("integrity", "black_frames", "silence", "loudness", "lip_sync_wer", "face_sim")

# Canonical schema v1 (BRIEF.md shared interface contract) — idempotent.
_SCHEMA = """
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


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class GateError(Exception):
    """Base error for the QA gate."""


class PolicyError(GateError):
    """Policy file missing/invalid (fail closed)."""


class LiveScoringDisabled(GateError):
    """Live scorer requested while the gate runs dark/stubbed."""


class DbError(GateError):
    """Job/DB state error (fail closed)."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _trunc(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_from_ratio(ratio: float, max_ratio: float) -> float:
    """1.0 at ratio 0, 0.0 at ratio == max_ratio, clamped [0, 1]."""
    ratio = max(0.0, float(ratio))
    max_ratio = float(max_ratio)
    if max_ratio <= 0.0:
        return 1.0 if ratio <= 0.0 else 0.0
    return clamp01(1.0 - ratio / max_ratio)


def _dedup(items) -> list:
    return list(dict.fromkeys(items))


# --------------------------------------------------------------------------- #
# policy
# --------------------------------------------------------------------------- #
_THRESHOLD_SPEC = {
    "min_duration_s": "number",
    "max_duration_s": "number",
    "duration_tolerance_s": "number",
    "max_black_ratio": "number",
    "max_silence_ratio": "number",
    "min_mean_volume_dbfs": "number",
    "require_audio": "bool",
    "accept_score": "number",
}
_DETECTOR_SPEC = {
    "blackdetect": {
        "min_black_duration_s": "number",
        "pixel_threshold": "number",
        "picture_black_ratio": "number",
    },
    "silencedetect": {
        "noise_floor_db": "number",
        "min_silence_duration_s": "number",
    },
    "decode_scan": {
        "max_seconds": "number",
        "timeout_s": "number",
    },
}


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _strip_comment(line: str) -> str:
    quote = None
    out: list = []
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _parse_scalar(text: str, lineno: int):
    t = text.strip()
    if t.startswith(("'", '"')):
        if len(t) < 2 or not t.endswith(t[0]):
            raise PolicyError(f"policy parse error: unterminated string (line {lineno})")
        return t[1:-1]
    low = t.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def _parse_flow_map(text: str, lineno: int) -> dict:
    if not text.endswith("}"):
        raise PolicyError(f"policy parse error: unterminated flow map (line {lineno})")
    inner = text[1:-1].strip()
    out: dict = {}
    if not inner:
        return out
    parts, cur, depth, quote = [], "", 0, None
    for ch in inner:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            cur += ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    for part in parts:
        k, sep, v = part.partition(":")
        k = k.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", k):
            raise PolicyError(f"policy parse error: bad flow entry {part.strip()!r} (line {lineno})")
        out[k] = _parse_scalar(v, lineno)
    return out


def _yaml_load_subset(text: str) -> dict:
    """Stdlib fallback parser for the documented policy subset (PyYAML preferred).

    Supports: block mappings by indentation, inline flow mappings with scalar
    values, comments, quoted/bare scalars, ints/floats/bools/null. Anything
    else raises PolicyError (fail closed).
    """
    root: dict = {}
    stack: list = [(-1, root)]
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if content.startswith("-"):
            raise PolicyError(f"policy parse error: sequences not supported (line {lineno})")
        if ":" not in content:
            raise PolicyError(f"policy parse error: expected key: value (line {lineno})")
        key, _, rest = content.partition(":")
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key):
            raise PolicyError(f"policy parse error: bad key {key!r} (line {lineno})")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise PolicyError(f"policy parse error: bad indentation (line {lineno})")
        parent = stack[-1][1]
        rest = rest.strip()
        if rest == "":
            node: dict = {}
            parent[key] = node
            stack.append((indent, node))
        elif rest.startswith("{"):
            parent[key] = _parse_flow_map(rest, lineno)
        else:
            parent[key] = _parse_scalar(rest, lineno)
    return root


@dataclass
class Policy:
    data: dict
    path: str
    sha256: str
    parser: str = "pyyaml"

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Policy":
        p = Path(path or DEFAULT_POLICY_PATH)
        if not p.is_file():
            raise PolicyError(f"policy file not found: {p}")
        raw = p.read_bytes()
        text = raw.decode("utf-8")
        parser = "pyyaml"
        try:
            import yaml  # preferred; stdlib fallback keeps the gate runnable without it
        except ImportError:
            yaml = None
        if yaml is not None:
            try:
                data = yaml.safe_load(text)
            except Exception as exc:  # noqa: BLE001 — fail closed on any parse problem
                raise PolicyError(f"policy parse failed: {p}: {exc}") from exc
        else:
            parser = "builtin"
            try:
                data = _yaml_load_subset(text)
            except PolicyError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise PolicyError(f"policy parse failed: {p}: {exc}") from exc
        if not isinstance(data, dict):
            raise PolicyError(f"policy root must be a mapping: {p}")
        for key, spec in _THRESHOLD_SPEC.items():
            if key not in data.get("thresholds", {}):
                raise PolicyError(f"policy missing thresholds.{key}")
            val = data["thresholds"][key]
            if spec == "number" and not _is_number(val):
                raise PolicyError(f"policy thresholds.{key} must be numeric, got {type(val).__name__}")
            if spec == "bool" and not isinstance(val, bool):
                raise PolicyError(f"policy thresholds.{key} must be boolean")
        checks = data.get("checks")
        if not isinstance(checks, dict):
            raise PolicyError("policy missing checks mapping")
        for cid in CHECK_IDS:
            cfg = checks.get(cid)
            if not isinstance(cfg, dict):
                raise PolicyError(f"policy missing checks.{cid}")
            if not isinstance(cfg.get("required"), bool):
                raise PolicyError(f"policy checks.{cid}.required must be boolean")
            if not _is_number(cfg.get("weight")) or cfg["weight"] < 0:
                raise PolicyError(f"policy checks.{cid}.weight must be a non-negative number")
        detectors = data.get("detectors")
        if not isinstance(detectors, dict):
            raise PolicyError("policy missing detectors mapping")
        for section, spec in _DETECTOR_SPEC.items():
            if not isinstance(detectors.get(section), dict):
                raise PolicyError(f"policy missing detectors.{section}")
            for key, kind in spec.items():
                val = detectors[section].get(key)
                if kind == "number" and not _is_number(val):
                    raise PolicyError(f"policy detectors.{section}.{key} must be numeric")
        return cls(data=data, path=str(p), sha256=hashlib.sha256(raw).hexdigest(), parser=parser)

    # convenience accessors
    @property
    def thresholds(self) -> dict:
        return self.data["thresholds"]

    @property
    def detectors(self) -> dict:
        return self.data["detectors"]

    def check_cfg(self, cid: str) -> dict:
        return self.data["checks"][cid]


def _resolve_policy(policy: Optional[Any]) -> Policy:
    if isinstance(policy, Policy):
        return policy
    if isinstance(policy, (str, Path)):
        return Policy.load(str(policy))
    if policy is None:
        return Policy.load()
    raise PolicyError(f"unsupported policy argument: {type(policy).__name__}")


# --------------------------------------------------------------------------- #
# local tool runners (ffprobe/ffmpeg only — never network)
# --------------------------------------------------------------------------- #
@dataclass
class _Cmd:
    rc: int
    out: str
    err: str


def _run(args: list, timeout: float) -> _Cmd:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
        )
        return _Cmd(proc.returncode, proc.stdout or "", proc.stderr or "")
    except subprocess.TimeoutExpired:
        return _Cmd(124, "", f"timeout after {timeout}s")
    except FileNotFoundError as exc:
        return _Cmd(127, "", f"tool not found: {exc}")


def probe_video(path: str, timeout: float = 60) -> dict:
    """ffprobe JSON probe. ok=False means the file is unreadable (fail closed)."""
    info: dict = {
        "ok": False,
        "error": None,
        "has_video": False,
        "has_audio": False,
        "video_codec": None,
        "audio_codec": None,
        "duration_s": None,
        "raw_error": "",
    }
    r = _run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        timeout,
    )
    if r.rc != 0:
        info["error"] = "probe_failed"
        info["raw_error"] = _trunc(r.err)
        return info
    try:
        data = json.loads(r.out or "{}")
    except json.JSONDecodeError:
        info["error"] = "probe_failed"
        info["raw_error"] = _trunc(r.out)
        return info
    streams = data.get("streams") or []
    if not streams:
        info["error"] = "probe_failed"
        info["raw_error"] = _trunc(r.err or r.out)
        return info
    for s in streams:
        if s.get("codec_type") == "video" and not info["has_video"]:
            info["has_video"] = True
            info["video_codec"] = s.get("codec_name")
        elif s.get("codec_type") == "audio" and not info["has_audio"]:
            info["has_audio"] = True
            info["audio_codec"] = s.get("codec_name")
    duration = None
    fmt = data.get("format") or {}
    for candidate in (fmt.get("duration"),) + tuple(
        s.get("duration") for s in streams if s.get("codec_type") == "video"
    ):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue
    info["duration_s"] = duration
    info["ok"] = True
    return info


def scan_decode(path: str, max_seconds: float = 0, timeout: float = 180) -> dict:
    """Full (or bounded) decode scan; any stderr line at error level = failure."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", path]
    if max_seconds and max_seconds > 0:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-f", "null", "-"]
    r = _run(cmd, timeout)
    lines = [ln.strip() for ln in r.err.splitlines() if ln.strip()]
    return {
        "ok": r.rc == 0 and not lines,
        "rc": r.rc,
        "errors": len(lines),
        "detail": _trunc(lines[-1]) if lines else "",
    }


def detect_black(path: str, cfg: dict, timeout: float = 180) -> list:
    vf = (
        f"blackdetect=d={cfg['min_black_duration_s']}:"
        f"pix_th={cfg['pixel_threshold']}:pic_th={cfg['picture_black_ratio']}"
    )
    r = _run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", path,
         "-vf", vf, "-an", "-f", "null", "-"],
        timeout,
    )
    segs = []
    pattern = re.compile(
        r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)\s+black_duration:\s*([0-9.]+)"
    )
    for m in pattern.finditer(r.err):
        start, end, dur = (float(x) for x in m.groups())
        if not any(abs(s["start"] - start) < 1e-3 and abs(s["end"] - end) < 1e-3 for s in segs):
            segs.append({"start": start, "end": end, "duration": dur})
    return segs


def detect_silence(path: str, cfg: dict, total_s: Optional[float], timeout: float = 180) -> list:
    af = f"silencedetect=noise={cfg['noise_floor_db']}dB:d={cfg['min_silence_duration_s']}"
    r = _run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", path,
         "-af", af, "-vn", "-f", "null", "-"],
        timeout,
    )
    segs: list = []
    open_start: Optional[float] = None
    start_re = re.compile(r"silence_start:\s*([0-9.]+)")
    end_re = re.compile(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)")
    for line in r.err.splitlines():
        m_end = end_re.search(line)
        if m_end:
            end, dur = float(m_end.group(1)), float(m_end.group(2))
            start = open_start if open_start is not None else max(0.0, end - dur)
            segs.append({"start": start, "end": end, "duration": dur})
            open_start = None
            continue
        m_start = start_re.search(line)
        if m_start:
            open_start = float(m_start.group(1))
    if open_start is not None:
        end = total_s if total_s and total_s > open_start else open_start
        if end > open_start:
            segs.append({"start": open_start, "end": end, "duration": end - open_start})
    return segs


def measure_loudness(path: str, timeout: float = 180) -> dict:
    r = _run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", path,
         "-af", "volumedetect", "-vn", "-f", "null", "-"],
        timeout,
    )
    mean_m = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", r.err)
    max_m = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", r.err)
    return {
        "mean_db": float(mean_m.group(1)) if mean_m else None,
        "max_db": float(max_m.group(1)) if max_m else None,
    }


# --------------------------------------------------------------------------- #
# metric IFACEs (stubbed in v1 — never fabricate)
# --------------------------------------------------------------------------- #
@dataclass
class MetricResult:
    name: str
    status: str  # ok | unavailable | error
    value: Optional[float]
    detail: str = ""


def word_error_rate(ref: str, hyp: str) -> float:
    """Word-level Levenshtein rate. Unicode-aware (Hindi scripts supported).

    Tokens keep letters + combining marks (Devanagari matras are marks, not
    ``\\w`` — stripping them would shatter Hindi words into fragments) and
    apostrophes; punctuation/symbols/control become separators.
    """

    def tokens(text: str) -> list:
        buf: list = []
        for ch in (text or "").lower():
            if ch == "'" or ch.isspace():
                buf.append(ch)
                continue
            if unicodedata.category(ch)[0] in ("P", "S", "C"):
                buf.append(" ")
            else:
                buf.append(ch)
        return "".join(buf).split()

    r, h = tokens(ref), tokens(hyp)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, start=1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1] / len(r)


class LipSyncWERScorer:
    """Lip-sync WER IFACE. v1 = stub (faster-whisper scorer plugs in on the box).

    Live mode refuses to even construct while the factory runs dark.
    """

    name = "lip_sync_wer"

    def __init__(self, mode: str = "stub") -> None:
        if mode == "live":
            raise LiveScoringDisabled(
                "lip_sync_wer live scorer is disabled in v1 (dark default); "
                "plug in the faster-whisper scorer on the render box"
            )
        if mode != "stub":
            raise GateError(f"unknown lip_sync_wer mode: {mode!r}")
        self.mode = mode

    def score(self, video_path: str, expected_text: Optional[str] = None) -> MetricResult:
        return MetricResult(
            name=self.name,
            status="unavailable",
            value=None,
            detail="stub IFACE (v1): no ASR executed; value never fabricated",
        )


class FaceSimScorer:
    """Face-similarity IFACE. v1 = stub (identity scorer plugs in on the box)."""

    name = "face_sim"

    def __init__(self, mode: str = "stub") -> None:
        if mode == "live":
            raise LiveScoringDisabled(
                "face_sim live scorer is disabled in v1 (dark default); "
                "plug in the identity scorer on the render box"
            )
        if mode != "stub":
            raise GateError(f"unknown face_sim mode: {mode!r}")
        self.mode = mode

    def score(self, video_path: str, refs_dir: Optional[str] = None) -> MetricResult:
        return MetricResult(
            name=self.name,
            status="unavailable",
            value=None,
            detail="stub IFACE (v1): no embedding computed; value never fabricated",
        )


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    id: str
    status: str  # pass | fail | unavailable | error
    required: bool
    weight: float
    score: Optional[float]
    value: dict = field(default_factory=dict)
    detail: str = ""
    codes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "required": self.required,
            "weight": self.weight,
            "score": self.score,
            "value": self.value,
            "detail": self.detail,
            "codes": self.codes,
        }


def _na_check(cid: str, policy: Policy, detail: str) -> CheckResult:
    """Not-applicable check (e.g. audio checks on a video-only input)."""
    cfg = policy.check_cfg(cid)
    return CheckResult(cid, "skipped", bool(cfg["required"]), float(cfg["weight"]),
                       None, {}, detail, [])


def _metric_check(cid: str, policy: Policy, metric: MetricResult) -> CheckResult:
    cfg = policy.check_cfg(cid)
    if metric.status == "unavailable":
        return CheckResult(cid, "unavailable", bool(cfg["required"]), float(cfg["weight"]),
                           None, {"metric_status": metric.status, "metric_value": None},
                           metric.detail, [])
    if metric.status == "error":
        return CheckResult(cid, "error", bool(cfg["required"]), float(cfg["weight"]),
                           None, {"metric_status": metric.status, "metric_value": None},
                           metric.detail, [f"{cid}_error"])
    value = metric.value
    score = clamp01(value) if value is not None else None
    return CheckResult(cid, "pass" if score is not None else "unavailable",
                       bool(cfg["required"]), float(cfg["weight"]), score,
                       {"metric_status": metric.status, "metric_value": value},
                       metric.detail, [])


def _integrity_check(policy: Policy, probe: dict, decode: Optional[dict],
                     expect_duration: Optional[float]) -> CheckResult:
    cfg = policy.check_cfg("integrity")
    th = policy.thresholds
    codes: list = []
    value = {
        "probe_ok": bool(probe.get("ok")),
        "has_video": bool(probe.get("has_video")),
        "has_audio": bool(probe.get("has_audio")),
        "video_codec": probe.get("video_codec"),
        "audio_codec": probe.get("audio_codec"),
        "duration_s": probe.get("duration_s"),
        "decode_errors": None,
    }
    detail = ""
    if not probe.get("ok"):
        codes.append(probe.get("error") or "probe_failed")
        detail = probe.get("raw_error") or "ffprobe could not parse the file"
    else:
        if not probe.get("has_video"):
            codes.append("no_video_stream")
        if th["require_audio"] and not probe.get("has_audio"):
            codes.append("no_audio_stream")
        duration = probe.get("duration_s")
        if duration is None:
            codes.append("duration_unknown")
        else:
            if duration < th["min_duration_s"]:
                codes.append("duration_below_min")
            if duration > th["max_duration_s"]:
                codes.append("duration_above_max")
            if expect_duration is not None and abs(duration - expect_duration) > th["duration_tolerance_s"]:
                codes.append("duration_mismatch")
        if decode is not None:
            value["decode_errors"] = decode["errors"]
            if not decode["ok"]:
                codes.append("decode_errors")
                detail = decode["detail"]
    status = "pass" if not codes else "fail"
    score = 1.0 if status == "pass" else None
    return CheckResult("integrity", status, bool(cfg["required"]), float(cfg["weight"]),
                       score, value, detail, codes)


def _black_check(policy: Policy, segments: list, duration: Optional[float]) -> CheckResult:
    cfg = policy.check_cfg("black_frames")
    th = policy.thresholds
    total = duration if duration and duration > 0 else max(
        [s["end"] for s in segments] + [1.0]
    )
    black_s = sum(s["duration"] for s in segments)
    ratio = clamp01(black_s / total)
    score = score_from_ratio(ratio, th["max_black_ratio"])
    codes = [] if ratio <= th["max_black_ratio"] else ["black_ratio_exceeded"]
    status = "pass" if not codes else "fail"
    value = {
        "black_ratio": round(ratio, 6),
        "black_seconds": round(black_s, 4),
        "segments": len(segments),
        "max_black_ratio": th["max_black_ratio"],
    }
    detail = (
        f"black {black_s:.2f}s of {total:.2f}s (ratio {ratio:.3f})"
        if segments else "no black segments detected"
    )
    return CheckResult("black_frames", status, bool(cfg["required"]), float(cfg["weight"]),
                       score, value, detail, codes)


def _silence_check(policy: Policy, probe_ok: bool, has_audio: bool, segments: list,
                   duration: Optional[float]) -> CheckResult:
    cfg = policy.check_cfg("silence")
    if not probe_ok:
        return CheckResult("silence", "skipped", bool(cfg["required"]), float(cfg["weight"]),
                           None, {}, "skipped: probe failed", [])
    if not has_audio:
        return CheckResult("silence", "skipped", bool(cfg["required"]), float(cfg["weight"]),
                           None, {}, "no audio stream (not applicable)", [])
    th = policy.thresholds
    total = duration if duration and duration > 0 else max(
        [s["end"] for s in segments] + [1.0]
    )
    silence_s = sum(s["duration"] for s in segments)
    ratio = clamp01(silence_s / total)
    score = score_from_ratio(ratio, th["max_silence_ratio"])
    codes = [] if ratio <= th["max_silence_ratio"] else ["silence_ratio_exceeded"]
    status = "pass" if not codes else "fail"
    value = {
        "silence_ratio": round(ratio, 6),
        "silence_seconds": round(silence_s, 4),
        "segments": len(segments),
        "max_silence_ratio": th["max_silence_ratio"],
    }
    detail = (
        f"silence {silence_s:.2f}s of {total:.2f}s (ratio {ratio:.3f})"
        if segments else "no silence segments detected"
    )
    return CheckResult("silence", status, bool(cfg["required"]), float(cfg["weight"]),
                       score, value, detail, codes)


def _loudness_check(policy: Policy, probe_ok: bool, has_audio: bool, loud: Optional[dict]) -> CheckResult:
    cfg = policy.check_cfg("loudness")
    th = policy.thresholds
    if not probe_ok or not has_audio or not loud:
        return CheckResult("loudness", "skipped", bool(cfg["required"]), float(cfg["weight"]),
                           None, {}, "no audio stream (not applicable)", [])
    mean_db = loud.get("mean_db")
    if mean_db is None:
        return CheckResult("loudness", "error", bool(cfg["required"]), float(cfg["weight"]),
                           None, {"mean_volume_dbfs": None, "max_volume_dbfs": loud.get("max_db"),
                                  "floor_dbfs": th["min_mean_volume_dbfs"]},
                           "volumedetect produced no mean_volume", ["loudness_error"])
    floor = th["min_mean_volume_dbfs"]
    score = clamp01(1.0 - max(0.0, floor - mean_db) / 15.0)
    codes = [] if mean_db >= floor else ["loudness_below_floor"]
    status = "pass" if not codes else "fail"
    value = {
        "mean_volume_dbfs": round(mean_db, 3),
        "max_volume_dbfs": round(loud["max_db"], 3) if loud.get("max_db") is not None else None,
        "floor_dbfs": floor,
    }
    return CheckResult("loudness", status, bool(cfg["required"]), float(cfg["weight"]),
                       score, value, f"mean {mean_db:.1f} dBFS (floor {floor:.1f})", codes)


# --------------------------------------------------------------------------- #
# evaluate
# --------------------------------------------------------------------------- #
def evaluate(
    video: str,
    policy: Optional[Any] = None,
    expect_duration: Optional[float] = None,
    scorers: Optional[dict] = None,
) -> dict:
    """Score one render. Pure/local: ffprobe+ffmpeg subprocesses only."""
    pol = _resolve_policy(policy)
    scorers = dict(scorers or {})
    if "lip_sync_wer" not in scorers:
        scorers["lip_sync_wer"] = LipSyncWERScorer(mode=pol.check_cfg("lip_sync_wer").get("mode", "stub"))
    if "face_sim" not in scorers:
        scorers["face_sim"] = FaceSimScorer(mode=pol.check_cfg("face_sim").get("mode", "stub"))

    video_path = Path(video).expanduser()
    video_abs = str(video_path.resolve()) if not video_path.is_absolute() else str(video_path)
    report: dict = {
        "gate_id": GATE_ID,
        "gate_version": __version__,
        "policy_id": pol.data.get("policy_id"),
        "policy_sha256": pol.sha256,
        "policy_parser": pol.parser,
        "video": video_abs,
        "video_sha256": None,
        "duration_s": None,
        "expected_duration_s": expect_duration,
        "evaluated_at": _now_iso(),
        "probe": {},
        "checks": [],
        "score": None,
        "score_basis": {"available_weight": 0.0, "total_weight": 0.0, "scored": []},
        "verdict": "reject",
        "codes": [],
        "reasons": [],
    }

    if not video_path.is_file():
        probe = {"ok": False, "error": "file_not_found", "has_video": False, "has_audio": False,
                 "video_codec": None, "audio_codec": None, "duration_s": None,
                 "decode_errors": None, "decode_ok": False, "raw_error": f"file not found: {video_abs}"}
        report["probe"] = _probe_summary(probe)
        checks = [_integrity_check(pol, probe, None, expect_duration)]
        checks += [_na_check(cid, pol, "skipped: input missing") for cid in CHECK_IDS[1:]]
    else:
        report["video_sha256"] = _sha256_file(video_path)
        probe = probe_video(video_abs)
        report["duration_s"] = probe.get("duration_s")
        decode = None
        if probe.get("ok"):
            dcfg = pol.detectors["decode_scan"]
            decode = scan_decode(
                video_abs,
                max_seconds=dcfg.get("max_seconds", 0),
                timeout=dcfg.get("timeout_s", 180),
            )
            probe["decode_errors"] = decode["errors"]
            probe["decode_ok"] = decode["ok"]
        report["probe"] = _probe_summary(probe)
        check_timeout = pol.detectors["decode_scan"].get("timeout_s", 180)

        if not probe.get("ok"):
            checks = [_integrity_check(pol, probe, None, expect_duration)]
            checks += [_na_check(cid, pol, "skipped: probe failed") for cid in CHECK_IDS[1:]]
        else:
            try:
                black_segs = detect_black(video_abs, pol.detectors["blackdetect"], check_timeout)
                black_check = _black_check(pol, black_segs, probe.get("duration_s"))
            except Exception as exc:  # noqa: BLE001
                black_check = _error_check("black_frames", pol, exc)
            try:
                sil_segs = (
                    detect_silence(video_abs, pol.detectors["silencedetect"],
                                   probe.get("duration_s"), check_timeout)
                    if probe.get("has_audio") else []
                )
                silence_check = _silence_check(pol, True, probe.get("has_audio"),
                                               sil_segs, probe.get("duration_s"))
            except Exception as exc:  # noqa: BLE001
                silence_check = _error_check("silence", pol, exc)
            try:
                loud = measure_loudness(video_abs, check_timeout) if probe.get("has_audio") else None
                loudness_check = _loudness_check(pol, True, probe.get("has_audio"), loud)
            except Exception as exc:  # noqa: BLE001
                loudness_check = _error_check("loudness", pol, exc)

            lip = _safe_metric(scorers["lip_sync_wer"], video_abs)
            face = _safe_metric(scorers["face_sim"], video_abs)
            checks = [
                _integrity_check(pol, probe, decode, expect_duration),
                black_check,
                silence_check,
                loudness_check,
                _metric_check("lip_sync_wer", pol, lip),
                _metric_check("face_sim", pol, face),
            ]

    report["checks"] = [c.to_dict() for c in checks]
    _decide(report, checks, pol)
    return report


def _probe_summary(probe: dict) -> dict:
    return {
        "ok": bool(probe.get("ok")),
        "error": probe.get("error"),
        "has_video": bool(probe.get("has_video")),
        "has_audio": bool(probe.get("has_audio")),
        "video_codec": probe.get("video_codec"),
        "audio_codec": probe.get("audio_codec"),
        "duration_s": probe.get("duration_s"),
        "decode_errors": probe.get("decode_errors"),
        "decode_ok": probe.get("decode_ok"),
        "raw_error": _trunc(probe.get("raw_error") or ""),
    }


def _error_check(cid: str, policy: Policy, exc: Exception) -> CheckResult:
    cfg = policy.check_cfg(cid)
    return CheckResult(cid, "error", bool(cfg["required"]), float(cfg["weight"]), None, {},
                       f"check crashed: {_trunc(str(exc), 200)}", [f"{cid}_error"])


def _safe_metric(scorer, video_abs: str) -> MetricResult:
    try:
        return scorer.score(video_abs)
    except Exception as exc:  # noqa: BLE001
        return MetricResult(name=getattr(scorer, "name", "metric"), status="error",
                            value=None, detail=f"scorer crashed: {_trunc(str(exc), 200)}")


def _decide(report: dict, checks: list, pol: Policy) -> None:
    codes: list = []
    reasons: list = []
    for c in checks:
        codes.extend(c.codes)
        if c.status in ("fail", "error"):
            reasons.append(f"{c.id}: {', '.join(c.codes) or c.status}"
                           + (f" — {c.detail}" if c.detail else ""))
        elif c.status == "unavailable" and c.required:
            reasons.append(f"{c.id}: required metric unavailable — {c.detail}")

    hard_fail = [c for c in checks if c.required and c.status in ("fail", "error")]
    required_unavailable = [c for c in checks if c.required and c.status == "unavailable"]
    scored = [c for c in checks if c.score is not None]
    available_weight = round(sum(c.weight for c in scored), 6)
    total_weight = round(sum(c.weight for c in checks), 6)
    score = None
    if available_weight > 0:
        score = round(
            100.0 * sum(c.weight * c.score for c in scored) / available_weight, 4
        )

    if hard_fail:
        verdict = "reject"
        codes.extend(c.codes[0] if c.codes else f"{c.id}_failed" for c in hard_fail)
    elif required_unavailable:
        verdict = "reject"
        codes.append("required_metric_unavailable")
    elif score is None:
        verdict = "reject"
        codes.append("no_scorable_checks")
    elif score + 1e-9 < pol.thresholds["accept_score"]:
        verdict = "reject"
        codes.append("below_accept_score")
        reasons.append(
            f"score {score} < accept_score {pol.thresholds['accept_score']}"
        )
    else:
        verdict = "accept"

    report["score"] = score
    report["score_basis"] = {
        "available_weight": available_weight,
        "total_weight": total_weight,
        "scored": [c.id for c in scored],
    }
    report["verdict"] = verdict
    report["codes"] = _dedup(codes)
    report["reasons"] = reasons


# --------------------------------------------------------------------------- #
# best-of-N
# --------------------------------------------------------------------------- #
def select_best_of_n(
    candidates: list,
    policy: Optional[Any] = None,
    expect_duration: Optional[float] = None,
    scorers: Optional[dict] = None,
) -> dict:
    """Score N candidates; return the highest-scoring eligible one (fail closed)."""
    pol = _resolve_policy(policy)
    cands = []
    for path in candidates:
        rep = evaluate(path, policy=pol, expect_duration=expect_duration, scorers=scorers)
        cands.append({
            "path": str(path),
            "verdict": rep["verdict"],
            "score": rep["score"],
            "eligible": rep["verdict"] == "accept",
            "codes": rep["codes"],
        })
    winner, winner_index, best = None, None, None
    for idx, c in enumerate(cands):
        if c["eligible"] and (best is None or (c["score"] is not None and c["score"] > best)):
            winner, winner_index, best = c["path"], idx, c["score"]
    return {
        "policy_id": pol.data.get("policy_id"),
        "winner": winner,
        "winner_index": winner_index,
        "reason": "winner_selected" if winner is not None else "no_eligible_candidate",
        "candidates": cands,
    }


# --------------------------------------------------------------------------- #
# factory DB (schema v1 — shows in BRIEF.md)
# --------------------------------------------------------------------------- #
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def _fetch_status(conn: sqlite3.Connection, job_id: str):
    try:
        return conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        raise DbError(f"schema_missing: {exc}") from exc


def record_result(db_path: str, job_id: str, report: dict, init_db: bool = False) -> dict:
    """Write gate_score/gate_json + status flip + event, per schema v1."""
    if not isinstance(report, dict):
        raise DbError("invalid_report: report must be a mapping")
    verdict = report.get("verdict")
    if verdict not in ("accept", "reject"):
        raise DbError(f"invalid_report: verdict={verdict!r}")
    score = report.get("score")
    if score is not None and not _is_number(score):
        raise DbError("invalid_report: score must be numeric or null")
    if verdict == "accept" and score is None:
        raise DbError("invalid_report: accept verdict requires a numeric score")

    conn = sqlite3.connect(str(db_path))
    try:
        if init_db:
            ensure_schema(conn)
        row = _fetch_status(conn, job_id)
        if row is None:
            raise DbError(f"unknown_job: {job_id}")
        status = row[0]
        if status not in ALLOWED_RECORD_STATES:
            raise DbError(f"invalid_state: {status} (allowed: {'/'.join(ALLOWED_RECORD_STATES)})")
        new_status = "gated" if verdict == "accept" else "rejected"
        event = "gate_passed" if verdict == "accept" else "gate_rejected"
        meta = json.dumps(
            {"score": score, "policy_id": report.get("policy_id"),
             "codes": report.get("codes", [])},
            sort_keys=True,
        )
        with conn:
            conn.execute(
                "UPDATE jobs SET status=?, gate_score=?, gate_json=? WHERE id=?",
                (new_status, float(score) if score is not None else None,
                 json.dumps(report, sort_keys=True, default=str), job_id),
            )
            conn.execute(
                "INSERT INTO events (job_id, ts, event, meta) VALUES (?,?,?,?)",
                (job_id, _now_iso(), event, meta),
            )
        return {"job_id": job_id, "status": new_status, "score": score,
                "event": event, "codes": report.get("codes", [])}
    finally:
        conn.close()


def gate_job(db_path: str, job_id: str, policy: Optional[Any] = None,
             expect_duration: Optional[float] = None, init_db: bool = False) -> dict:
    """Read render_path from the job, evaluate it, record the gate outcome."""
    pol = _resolve_policy(policy)
    conn = sqlite3.connect(str(db_path))
    try:
        if init_db:
            ensure_schema(conn)
        try:
            row = conn.execute("SELECT render_path FROM jobs WHERE id=?", (job_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            raise DbError(f"schema_missing: {exc}") from exc
    finally:
        conn.close()
    if row is None:
        raise DbError(f"unknown_job: {job_id}")
    render_path = row[0]
    if not render_path:
        raise DbError(f"no_render_path: {job_id}")
    report = evaluate(render_path, policy=pol, expect_duration=expect_duration)
    rec = record_result(db_path, job_id, report, init_db=init_db)
    out = dict(rec)
    out["report"] = report
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_report(rep: dict, stream=sys.stdout) -> None:
    duration = f"{rep['duration_s']}s" if rep["duration_s"] is not None else "n/a"
    print(f"verdict: {rep['verdict']}  score: {rep['score']}", file=stream)
    print(f"video: {rep['video']}  ({duration})", file=stream)
    for c in rep["checks"]:
        print(f"  - {c['id']}: {c['status']}" + (f" [{', '.join(c['codes'])}]" if c["codes"] else ""),
              file=stream)
    if rep["reasons"]:
        for r in rep["reasons"]:
            print(f"  ! {r}", file=stream)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="gate.py", description="WTF QA Gate v1 (dark/local)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_score = sub.add_parser("score", help="score one render")
    p_score.add_argument("--video", required=True)
    p_score.add_argument("--policy", default=None)
    p_score.add_argument("--expect-duration", type=float, default=None)
    p_score.add_argument("--json", dest="json_out", default=None)
    p_score.add_argument("--quiet", action="store_true")

    p_best = sub.add_parser("bestof", help="best-of-N selection")
    p_best.add_argument("--candidates", required=True,
                        help="comma-separated candidate paths")
    p_best.add_argument("--policy", default=None)
    p_best.add_argument("--expect-duration", type=float, default=None)
    p_best.add_argument("--json", dest="json_out", default=None)

    p_rec = sub.add_parser("record", help="record a saved report against a job")
    p_rec.add_argument("--db", default=DEFAULT_DB_PATH)
    p_rec.add_argument("--job", required=True)
    p_rec.add_argument("--report", required=True)
    p_rec.add_argument("--init-db", action="store_true")

    p_run = sub.add_parser("run", help="evaluate a job's render_path and record")
    p_run.add_argument("--db", default=DEFAULT_DB_PATH)
    p_run.add_argument("--job", required=True)
    p_run.add_argument("--policy", default=None)
    p_run.add_argument("--expect-duration", type=float, default=None)
    p_run.add_argument("--init-db", action="store_true")

    sub.add_parser("selftest", help="report local tool availability")
    sub.add_parser("version", help="print gate id/version")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "version":
            print(f"{GATE_ID} {__version__}")
            return 0
        if args.cmd == "selftest":
            ok = True
            for tool in ("ffprobe", "ffmpeg"):
                path = shutil.which(tool)
                print(f"{tool}: {path or 'MISSING'}")
                if path:
                    r = _run([tool, "-version"], 20)
                    print(f"  {_trunc(r.out.splitlines()[0] if r.out else r.err, 120)}")
                else:
                    ok = False
            print(f"policy: {Policy.load().data.get('policy_id')} @ {DEFAULT_POLICY_PATH}")
            return 0 if ok else 1
        if args.cmd == "score":
            pol = Policy.load(args.policy) if args.policy else None
            rep = evaluate(args.video, policy=pol, expect_duration=args.expect_duration)
            if args.json_out:
                Path(args.json_out).write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")
            if not args.quiet:
                _print_report(rep)
            return 0 if rep["verdict"] == "accept" else 2
        if args.cmd == "bestof":
            pol = Policy.load(args.policy) if args.policy else None
            cands = [c.strip() for c in args.candidates.split(",") if c.strip()]
            sel = select_best_of_n(cands, policy=pol, expect_duration=args.expect_duration)
            if args.json_out:
                Path(args.json_out).write_text(json.dumps(sel, indent=2, sort_keys=True) + "\n")
            if sel["winner"] is not None:
                print(f"winner: {sel['winner']}")
                print(f"  score: {sel['candidates'][sel['winner_index']]['score']}")
            else:
                print(f"no eligible candidate ({sel['reason']})")
                for c in sel["candidates"]:
                    print(f"  - {c['path']}: {c['verdict']} {c['codes']}")
            return 0 if sel["winner"] is not None else 2
        if args.cmd == "record":
            rep = json.loads(Path(args.report).read_text())
            res = record_result(args.db, args.job, rep, init_db=args.init_db)
            print(f"recorded {res['job_id']}: {res['status']} (event {res['event']}, score {res['score']})")
            return 0
        if args.cmd == "run":
            pol = Policy.load(args.policy) if args.policy else None
            res = gate_job(args.db, args.job, policy=pol,
                           expect_duration=args.expect_duration, init_db=args.init_db)
            _print_report(res["report"])
            print(f"recorded {res['job_id']}: {res['status']} (event {res['event']})")
            return 0 if res["report"]["verdict"] == "accept" else 2
    except GateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — never crash on unexpected input
        print(f"ERROR: unexpected: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
