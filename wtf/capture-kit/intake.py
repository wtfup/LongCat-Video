#!/usr/bin/env python3
"""W6 Capture Kit — intake organiser for the WTF Avatar Factory.

Organises raw camera-card footage into the factory asset layout:

    <dest>/assets/refs/         reference stills + 15s neutral clips (+ any images)
    <dest>/assets/voice/        voice-corpus audio (all audio extensions)
    <dest>/assets/face_clips/   talking-head performance video (monologues, casual talk)
    <dest>/manifest.json        machine manifest of everything that happened

Design rules (see README.md for the full contract):

* Local-only. No network calls anywhere. ffprobe/ffmpeg are local binaries.
* Copy by default — camera originals are never touched unless ``--move`` is passed.
* ``--dry-run`` writes nothing at all (it still reads files to plan exactly).
* Validation fails closed: if ``--validate`` is on (default) and ffprobe is
  missing, the run stops with exit code 2 *before* creating anything.
* Warnings (wrong duration/resolution/fps/sample-rate) never fail a run; only
  real errors (zero bytes, unreadable/undecodable media, probe failure) do.

CLI exit codes: 0 ok (warnings allowed) · 1 errors · 2 usage / fail-closed gate.

Usage:
    python3 intake.py --source RAW_DIR --dest ASSETS_ROOT [--dry-run] [--move]
                      [--no-validate] [--no-hash] [--ffprobe PATH] [--json]
    python3 intake.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

SCHEMA = "wtf.capture-kit.manifest/v1"
TOOL = "wtf-capture-kit/intake.py"
ASSETS_DIRNAME = "assets"
MANIFEST_NAME = "manifest.json"

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi", ".mts", ".m2ts", ".mpg", ".mpeg"}
AUDIO_EXTS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".aif", ".aiff", ".ogg", ".opus"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp", ".dng"}

# content token -> media kind + destination bucket (naming convention in CAPTURE_CHECKLIST.md)
CONTENT_KINDS = {
    "still": {"kind": "image", "bucket": "refs"},
    "neutral": {"kind": "video", "bucket": "refs"},
    "hindi_mono": {"kind": "video", "bucket": "face_clips"},
    "english_mono": {"kind": "video", "bucket": "face_clips"},
    "casual": {"kind": "video", "bucket": "face_clips"},
}

# expected duration window (seconds) per content token: (min, max)
EXPECTED_DURATION = {
    "neutral": (8.0, 30.0),
    "hindi_mono": (60.0, 240.0),
    "english_mono": (60.0, 240.0),
    "casual": (300.0, 1800.0),
}

JUNK_NAMES = {"thumbs.db", "desktop.ini", "icon\r"}
OUTFIT_RE = re.compile(r"^(?:o|outfit)([1-9])$")
TAKE_RE = re.compile(r"^(?:t|take)(\d+)$")
TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")

MIN_FPS = 24.0
MIN_WIDTH_WARN = 1920   # below this -> warn (spec: capture 4K, 1080p proxy at minimum)
MIN_WIDTH_4K_INFO = 3840
MIN_SAMPLE_RATE = 44100


# --------------------------------------------------------------------------- helpers


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _norm(path) -> Path:
    return Path(path).expanduser().resolve()


def _binary_ok(binary: str) -> bool:
    """True when `binary` resolves to an executable — path or PATH lookup."""
    if not binary:
        return False
    if os.sep in binary or (os.altsep and os.altsep in binary):
        p = Path(binary).expanduser()
        return p.is_file() and os.access(str(p), os.X_OK)
    return shutil.which(binary) is not None


def find_ffprobe() -> Optional[str]:
    return shutil.which("ffprobe")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fps_from(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        if "/" in raw:
            num, den = raw.split("/", 1)
            den_f = float(den)
            if den_f == 0:
                return None
            return round(float(num) / den_f, 3)
        return round(float(raw), 3)
    except (TypeError, ValueError):
        return None


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- classification


def classify(name: str) -> dict:
    """Route one filename to a bucket using the documented naming convention.

    Recognised:  WTF_<yyyymmdd>_o<n>_<f|s>_<content>[_t<n>].<ext>
    Fallback:    media with a known extension but no content token is routed by
                 extension only and flagged `extension-fallback`.
    Unknown extensions are skipped; OS junk files are ignored.
    """
    lowered = name.lower()
    if name.startswith(".") or lowered in JUNK_NAMES:
        return {"action": "ignore", "reason": "system-junk", "bucket": None,
                "content": None, "outfit": None, "angle": None, "take": None,
                "classified_by": None, "ext": Path(name).suffix.lower(), "media": None}

    ext = Path(name).suffix.lower()
    if ext in VIDEO_EXTS:
        media = "video"
    elif ext in AUDIO_EXTS:
        media = "audio"
    elif ext in IMAGE_EXTS:
        media = "image"
    else:
        return {"action": "skip", "reason": "unrecognized-extension", "bucket": None,
                "content": None, "outfit": None, "angle": None, "take": None,
                "classified_by": None, "ext": ext, "media": None}

    stem = Path(name).stem.lower()
    tokens = [t for t in TOKEN_SPLIT_RE.split(stem) if t]

    # content tokens may span multiple tokens (e.g. "hindi_mono" -> ["hindi", "mono"])
    content = None
    for i in range(len(tokens)):
        for j in (i + 1, i + 2):
            candidate = "_".join(tokens[i:j])
            if candidate in CONTENT_KINDS:
                content = candidate
                break
        if content:
            break

    outfit = None
    for token in tokens:
        m = OUTFIT_RE.match(token)
        if m:
            outfit = "o" + m.group(1)
            break

    angle = None
    for token in tokens:
        if token in ("f", "front"):
            angle = "front"
            break
        if token in ("s", "side"):
            angle = "side"
            break

    take = None
    for token in tokens:
        m = TAKE_RE.match(token)
        if m:
            take = int(m.group(1))
            break

    if media == "audio":
        bucket = "voice"
    elif content in ("still", "neutral"):
        bucket = "refs"
    elif content in ("hindi_mono", "english_mono", "casual"):
        bucket = "face_clips" if media == "video" else "refs"
    else:
        bucket = {"video": "face_clips", "audio": "voice", "image": "refs"}[media]

    return {
        "action": "ingest",
        "reason": None,
        "bucket": bucket,
        "content": content,
        "outfit": outfit,
        "angle": angle,
        "take": take,
        "classified_by": "token" if content else "extension-fallback",
        "ext": ext,
        "media": media,
    }


def iter_source_files(source: Path, dest: Path) -> list:
    """Recursive, deterministic file listing.

    Skips hidden directories and the destination tree; hidden files are listed
    so classify() can record them as ignored (auditable junk accounting).
    """
    out = []
    dest_resolved = dest.resolve()
    for p in sorted(source.rglob("*"), key=lambda q: str(q)):
        parts = p.relative_to(source).parts[:-1]  # directory parts only
        if any(part.startswith(".") for part in parts):
            continue
        if not p.is_file():
            continue
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp == dest_resolved or dest_resolved in rp.parents:
            continue
        out.append(p)
    return out


# --------------------------------------------------------------------------- probing


def probe_media(path: Path, ffprobe_bin: str = "ffprobe", timeout: float = 30.0) -> dict:
    """Read technical metadata via a local ffprobe call (no network)."""
    if not _binary_ok(ffprobe_bin):
        return {"ok": False, "error": "ffprobe-not-found"}
    cmd = [ffprobe_bin, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env dependent
        return {"ok": False, "error": "ffprobe-exec-failed: %s" % exc}
    if cp.returncode != 0:
        return {"ok": False, "error": "ffprobe-exit-%d: %s" % (cp.returncode, (cp.stderr or "").strip()[:200])}
    try:
        data = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "ffprobe-invalid-json"}

    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}

    duration = _to_float(fmt.get("duration"))
    if duration is None and v is not None:
        duration = _to_float(v.get("duration"))
    if duration is None and a is not None:
        duration = _to_float(a.get("duration"))

    return {
        "ok": True,
        "error": None,
        "duration_s": duration,
        "has_video": v is not None,
        "has_audio": a is not None,
        "width": int(v["width"]) if v and v.get("width") else None,
        "height": int(v["height"]) if v and v.get("height") else None,
        "fps": _fps_from(v.get("r_frame_rate") or v.get("avg_frame_rate")) if v else None,
        "vcodec": v.get("codec_name") if v else None,
        "acodec": a.get("codec_name") if a else None,
        "sample_rate": int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        "channels": int(a["channels"]) if a and a.get("channels") else None,
        "format_name": fmt.get("format_name"),
    }


def build_checks(cls: dict, probe: Optional[dict], size: int) -> list:
    """Non-fatal validation checks; returns list of {id, level, msg}."""
    checks = []
    if cls.get("classified_by") == "extension-fallback":
        checks.append({"id": "content-token-missing", "level": "warn",
                       "msg": "no content token in filename; routed by extension only "
                              "(see CAPTURE_CHECKLIST.md naming convention)"})

    content = cls.get("content")
    media = cls.get("media")
    if content and media == "audio":
        # audio extension always routes to voice by design; note it, don't warn
        checks.append({"id": "audio-extension-wins", "level": "info",
                       "msg": "audio extension routes to voice/ regardless of content token"})
    elif content and CONTENT_KINDS[content]["kind"] != media:
        checks.append({"id": "content-media-mismatch", "level": "warn",
                       "msg": "content token %r expects %s but file is %s"
                              % (content, CONTENT_KINDS[content]["kind"], media)})

    if not probe or not probe.get("ok"):
        return checks

    window = EXPECTED_DURATION.get(content or "")
    dur = probe.get("duration_s")
    if window and dur is not None:
        if dur < window[0] or dur > window[1]:
            checks.append({"id": "duration-out-of-range", "level": "warn",
                           "msg": "duration %.1fs outside expected %.0f-%.0fs for %s"
                                  % (dur, window[0], window[1], content)})

    if probe.get("has_video"):
        width = probe.get("width")
        if width:
            if width < MIN_WIDTH_WARN:
                checks.append({"id": "resolution-below-1080p", "level": "warn",
                               "msg": "width %dpx below 1920px — re-shoot at 4K (or 1080p minimum)" % width})
            elif width < MIN_WIDTH_4K_INFO:
                checks.append({"id": "resolution-not-4k", "level": "info",
                               "msg": "width %dpx — spec target is 4K 3840px" % width})
        fps = probe.get("fps")
        if fps is not None and fps < MIN_FPS:
            checks.append({"id": "fps-below-24", "level": "warn",
                           "msg": "fps %s below 24 — check camera frame-rate setting" % fps})

    if probe.get("has_audio"):
        rate = probe.get("sample_rate")
        if rate is not None and rate < MIN_SAMPLE_RATE:
            checks.append({"id": "sample-rate-below-44k", "level": "warn",
                           "msg": "sample rate %dHz below 44100Hz (voice_requirements.md wants 48kHz)" % rate})
        ch = probe.get("channels")
        if ch is not None and ch != 1:
            checks.append({"id": "not-mono", "level": "info",
                           "msg": "%d channels — mono preferred for the voice corpus" % ch})
    elif cls.get("bucket") == "voice":
        checks.append({"id": "missing-audio-stream", "level": "error",
                       "msg": "voice-bucket file has no audio stream"})

    return checks


# --------------------------------------------------------------------------- organise


class IntakeError(Exception):
    """Raised for usage-level failures (bad paths, missing ffprobe)."""


def _resolve_probe_fn(validate: bool, ffprobe_bin: Optional[str],
                      probe_fn: Optional[Callable]) -> tuple:
    """Returns (probe_fn, error). Fails closed when validation is required."""
    if not validate:
        return (None, None)
    if probe_fn is not None:
        return (probe_fn, None)
    binary = ffprobe_bin or find_ffprobe()
    if not binary or not _binary_ok(binary):
        return (None, "ffprobe-not-found: install ffmpeg (provides ffprobe) or pass --no-validate")
    return (lambda p: probe_media(p, ffprobe_bin=binary), None)


def organize(source, dest, *, dry_run: bool = False, move: bool = False,
             validate: bool = True, ffprobe_bin: Optional[str] = None,
             probe_fn: Optional[Callable] = None, hash_files: bool = True) -> dict:
    """Plan (dry-run) or execute (real) an intake run. Returns a result dict."""
    src = _norm(source)
    dst_root = _norm(dest)
    mode = "move" if move else "copy"

    result = {
        "schema": SCHEMA, "tool": TOOL, "created_at": _utcnow(),
        "dry_run": bool(dry_run), "mode": mode, "validate": bool(validate),
        "source_root": str(src), "dest_root": str(dst_root),
        "exit_code": 0, "files": [], "skipped": [], "ignored": [],
        "errors": [], "warnings": [], "summary": {}, "manifest_path": None,
    }

    def fail(code: int, reason: str, detail: str = "") -> dict:
        result["errors"].append({"src": None, "reason": reason, "detail": detail})
        result["exit_code"] = code
        result["summary"] = _summarize(result)
        return result

    if not src.exists() or not src.is_dir():
        return fail(2, "source-not-found", "source is not a directory: %s" % src)
    if dst_root == src or src in dst_root.parents:
        return fail(2, "dest-inside-source", "dest %s is inside source %s" % (dst_root, src))
    if dst_root in src.parents:
        return fail(2, "source-inside-dest", "source %s is inside dest %s" % (src, dst_root))

    probe_fn_resolved, probe_error = _resolve_probe_fn(validate, ffprobe_bin, probe_fn)
    if probe_error:
        return fail(2, "ffprobe-not-found", probe_error + " (fail-closed: nothing written)")

    examined = 0
    for f in iter_source_files(src, dst_root):
        examined += 1
        cls = classify(f.name)
        if cls["action"] == "ignore":
            result["ignored"].append({"src": str(f), "reason": cls["reason"]})
            continue
        if cls["action"] == "skip":
            result["skipped"].append({"src": str(f), "reason": cls["reason"]})
            continue

        entry = {
            "src": str(f),
            "dst": None,
            "rel": None,
            "bucket": cls["bucket"],
            "content": cls["content"],
            "outfit": cls["outfit"],
            "angle": cls["angle"],
            "take": cls["take"],
            "classified_by": cls["classified_by"],
            "sha256": None,
            "bytes": 0,
            "probe": None,
            "checks": [],
            "dedupe_existing": False,
        }

        try:
            size = f.stat().st_size
        except OSError as exc:
            result["errors"].append({"src": str(f), "reason": "stat-failed", "detail": str(exc)})
            continue
        entry["bytes"] = size

        if size == 0:
            result["errors"].append({"src": str(f), "reason": "zero-bytes", "detail": "empty file"})
            continue

        probe = probe_fn_resolved(f) if probe_fn_resolved else None
        entry["probe"] = probe
        if validate and probe is not None and not probe.get("ok"):
            result["errors"].append({"src": str(f), "reason": "probe-failed",
                                     "detail": str(probe.get("error"))})
            continue

        checks = build_checks(cls, probe, size)
        entry["checks"] = checks
        if any(c["level"] == "error" for c in checks):
            for c in checks:
                if c["level"] == "error":
                    result["errors"].append({"src": str(f), "reason": c["id"], "detail": c["msg"]})
            continue

        if hash_files:
            try:
                entry["sha256"] = sha256_file(f)
            except OSError as exc:
                result["errors"].append({"src": str(f), "reason": "hash-failed", "detail": str(exc)})
                continue

        target_dir = dst_root / ASSETS_DIRNAME / cls["bucket"]
        dst = target_dir / f.name

        if dst.exists() and dst.is_file():
            same = False
            if entry["sha256"] is not None and hash_files:
                try:
                    same = sha256_file(dst) == entry["sha256"]
                except OSError:
                    same = False
            if same:
                entry["dedupe_existing"] = True
                entry["dst"] = str(dst)
                entry["rel"] = str(dst.relative_to(dst_root))
                checks.append({"id": "duplicate-content", "level": "warn",
                               "msg": "identical content already ingested; skipped copy"})
                result["files"].append(entry)
                if not dry_run and move:
                    checks.append({"id": "dedupe-source-kept", "level": "info",
                                   "msg": "move mode: source kept because destination already had this content"})
                continue
            dst = _unique_name(target_dir, f.stem, f.suffix)
            checks.append({"id": "name-collision-renamed", "level": "warn",
                           "msg": "name already present with different content; stored as %s" % dst.name})

        entry["dst"] = str(dst)
        entry["rel"] = str(dst.relative_to(dst_root))

        if not dry_run:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if move:
                    shutil.move(str(f), str(dst))
                else:
                    shutil.copy2(str(f), str(dst))
            except OSError as exc:
                result["errors"].append({"src": str(f), "reason": "copy-failed", "detail": str(exc)})
                entry["dst"] = None
                entry["rel"] = None
                continue

        result["files"].append(entry)

    # run-level warnings
    result["_examined"] = examined
    ingested = sum(1 for f in result["files"] if not f["dedupe_existing"])
    if result["files"] and ingested == 0 and not result["errors"]:
        result["warnings"].append({"src": None, "check_id": "no-new-media",
                                   "msg": "run produced no new ingested files (all deduped?)"})
    if not result["files"] and (result["skipped"] or result["ignored"]) and not result["errors"]:
        result["warnings"].append({"src": None, "check_id": "no-media-ingested",
                                   "msg": "no media files were recognised; check the naming convention"})

    # flatten warn-level checks
    for f in result["files"]:
        for c in f["checks"]:
            if c["level"] == "warn":
                result["warnings"].append({"src": f["src"], "check_id": c["id"], "msg": c["msg"]})

    result["summary"] = _summarize(result)
    result["exit_code"] = 1 if result["errors"] else 0
    result.pop("_examined", None)

    if not dry_run:
        manifest = {
            "schema": SCHEMA, "tool": TOOL, "created_at": _utcnow(),
            "source_root": str(src), "dest_root": str(dst_root), "mode": mode,
            "validate": bool(validate), "hash_files": bool(hash_files),
            "files": result["files"], "skipped": result["skipped"],
            "ignored": result["ignored"], "errors": result["errors"],
            "warnings": result["warnings"], "summary": result["summary"],
        }
        dst_root.mkdir(parents=True, exist_ok=True)
        manifest_path = dst_root / MANIFEST_NAME
        tmp_path = dst_root / (MANIFEST_NAME + ".tmp")
        tmp_path.write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(str(tmp_path), str(manifest_path))
        result["manifest_path"] = str(manifest_path)

    return result


def _unique_name(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / (stem + suffix)
    n = 2
    while candidate.exists():
        candidate = directory / ("%s__dup%d%s" % (stem, n, suffix))
        n += 1
    return candidate


def _summarize(result: dict) -> dict:
    files = result.get("files", [])
    by_bucket = {"refs": 0, "voice": 0, "face_clips": 0}
    deduped = 0
    for f in files:
        by_bucket[f["bucket"]] = by_bucket.get(f["bucket"], 0) + 1
        if f.get("dedupe_existing"):
            deduped += 1
    seen = result.get("_examined")
    if seen is None:
        seen = len(files) + len(result.get("skipped", [])) + len(result.get("ignored", []))
    return {
        "seen": seen,
        "ingested": len(files) - deduped,
        "deduped": deduped,
        "skipped": len(result.get("skipped", [])),
        "ignored": len(result.get("ignored", [])),
        "errors": len(result.get("errors", [])),
        "warnings": len([w for w in result.get("warnings", [])]),
        "by_bucket": by_bucket,
    }


# --------------------------------------------------------------------------- CLI


def _print_human(result: dict, stream=None) -> None:
    # resolve at call time so pytest/redirect capture works
    if stream is None:
        stream = sys.stdout
    mode = "DRY RUN" if result["dry_run"] else "RUN"
    print("[capture-kit] %s — source=%s dest=%s mode=%s validate=%s"
          % (mode, result["source_root"], result["dest_root"], result["mode"], result["validate"]), file=stream)
    for f in result["files"]:
        tag = " (dedupe: already present)" if f.get("dedupe_existing") else ""
        print("  %-11s %s <- %s%s" % (f["bucket"], f["rel"], f["src"], tag), file=stream)
    for s in result["skipped"]:
        print("  skip      %s (%s)" % (s["src"], s["reason"]), file=stream)
    for i in result["ignored"]:
        print("  ignore    %s (%s)" % (i["src"], i["reason"]), file=stream)
    for e in result["errors"]:
        print("  ERROR     %s: %s" % (e["reason"], e.get("detail") or e.get("src")), file=stream)
    for w in result["warnings"]:
        print("  warn      %s: %s" % (w["check_id"], w["msg"]), file=stream)
    s = result["summary"]
    print("  summary: seen=%s ingested=%s deduped=%s skipped=%s ignored=%s errors=%s warnings=%s"
          % (s["seen"], s["ingested"], s["deduped"], s["skipped"], s["ignored"], s["errors"], s["warnings"]), file=stream)
    if result.get("manifest_path"):
        print("  manifest: %s" % result["manifest_path"], file=stream)
    if result["dry_run"]:
        print("  (dry run — nothing was written)", file=stream)


def _self_test() -> int:
    """Offline smoke test: classification table + dry-run purity in a temp dir."""
    failures = []

    def check(label: str, cond: bool) -> None:
        print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
        if not cond:
            failures.append(label)

    cases = [
        ("WTF_20260914_o1_f_neutral_t1.mp4", "refs", "neutral"),
        ("WTF_20260914_o2_f_hindi_mono_t1.mp4", "face_clips", "hindi_mono"),
        ("WTF_20260914_o2_f_english_mono_t1.mp4", "face_clips", "english_mono"),
        ("WTF_20260914_o3_f_casual_t1.mov", "face_clips", "casual"),
        ("WTF_20260914_o1_f_still_01.jpg", "refs", "still"),
        ("WTF_20260914_o1_f_casual_t1.wav", "voice", "casual"),
    ]
    for name, bucket, content in cases:
        c = classify(name)
        check("%s -> %s/%s" % (name, bucket, content),
              c["action"] == "ingest" and c["bucket"] == bucket and c["content"] == content)
    check("unknown extension skipped", classify("notes.pdf")["action"] == "skip")
    check("junk ignored", classify(".DS_Store")["action"] == "ignore")

    with tempfile.TemporaryDirectory(prefix="wtf-capture-kit-selftest-") as td:
        root = Path(td)
        raw = root / "raw"
        raw.mkdir()
        (raw / "WTF_20260914_o1_f_still_01.jpg").write_bytes(b"img")
        dest = root / "assets-root"
        res = organize(raw, dest, dry_run=True, validate=False)
        check("dry run plans 1 file", len(res["files"]) == 1)
        check("dry run writes nothing", not dest.exists())
        check("dry run exit 0", res["exit_code"] == 0)

    print("[capture-kit] self-test: %s" % ("PASS" if not failures else "FAIL (%d)" % len(failures)))
    return 0 if not failures else 1


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intake.py",
        description="W6 Capture Kit intake — organise raw footage into assets/{refs,voice,face_clips}.",
    )
    parser.add_argument("--source", help="raw footage directory (camera card dump)")
    parser.add_argument("--dest", help="assets root; output lands in <dest>/assets/... and <dest>/manifest.json")
    parser.add_argument("--dry-run", action="store_true", help="plan everything, write nothing, touch nothing")
    parser.add_argument("--move", action="store_true", help="move files instead of copying (opt-in; originals deleted)")
    parser.add_argument("--no-validate", action="store_true", help="skip ffprobe validation (not recommended)")
    parser.add_argument("--no-hash", action="store_true", help="skip sha256 hashing (faster on huge corpora)")
    parser.add_argument("--ffprobe", default=None, help="path to ffprobe binary (default: PATH lookup)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON on stdout")
    parser.add_argument("--self-test", action="store_true", help="run offline self-test (no args needed)")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not args.source or not args.dest:
        parser.error("--source and --dest are required (or use --self-test)")

    result = organize(
        args.source, args.dest,
        dry_run=args.dry_run, move=args.move, validate=not args.no_validate,
        ffprobe_bin=args.ffprobe, hash_files=not args.no_hash,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result)
        for e in result["errors"]:
            print("[capture-kit] error: %s %s" % (e["reason"], e.get("detail") or ""), file=sys.stderr)

    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
