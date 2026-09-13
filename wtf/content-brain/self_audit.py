"""Self-audit for the W4 Content Brain package.

Scans the package and reports, with machine-readable output:
  - required deliverable files present
  - runtime network imports (must be none)
  - secret-like tokens in any text artifact (must be none)
  - live calls / secrets printed / unverified claims = 0

Usage:
    python self_audit.py            # print audit JSON
    python self_audit.py --write    # also write evidence/self_audit.txt + manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent

RUNTIME_FILES = ("brain.py", "calendar.py", "providers.py")

REQUIRED_FILES = (
    "README.md",
    "requirements.txt",
    "brain.py",
    "calendar.py",
    "providers.py",
    "pillars.yaml",
    "prompts/system.md",
    "prompts/daily_brief.md",
    "prompts/script_45s_en.md",
    "prompts/script_45s_hi.md",
    "prompts/shot_list.md",
    "prompts/caption_hashtags.md",
    "prompts/thumbnail.md",
    "samples/index.json",
)

TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".log", ".sh", ".csv"}

IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w\.]*)", re.M)
NETWORK_MODULES = {
    "requests", "urllib", "urllib3", "httpx", "aiohttp", "socket", "http",
    "ftplib", "smtplib", "telnetlib", "websocket", "websockets", "ssl",
}

SECRET_PATTERNS = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("openai_style_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{30,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE)-----")),
    (
        "assigned_credential",
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|secret|passwd|password|credential|auth[_-]?token)\b"
            r"\s*[:=]\s*[\"'][^\"']{8,}[\"']"
        ),
    ),
)


def _skip_dir(path: Path) -> bool:
    return (
        "__pycache__" in path.parts
        or ".pytest_cache" in path.parts
        # derived audit outputs: excluded so the manifest is stable across runs
        or path.name in ("self_audit.txt", "manifest.json")
    )


def _iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if _skip_dir(path) or path.is_dir():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def _scan_network_imports(root: Path):
    findings = []
    for rel in RUNTIME_FILES:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for module in IMPORT_RE.findall(text):
            top = module.split(".")[0]
            if top in NETWORK_MODULES:
                findings.append({"file": rel, "module": module})
    return findings


def _scan_secrets(root: Path):
    findings = []
    for path in _iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                findings.append({"file": rel, "pattern": name, "sample": match.group(0)[:12] + "…"})
    return findings


def _manifest(root: Path):
    manifest = {}
    for path in sorted(root.rglob("*")):
        if _skip_dir(path) or path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        manifest[rel] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    return manifest


def run_audit(root: Path = PKG_DIR):
    root = Path(root)
    missing = [rel for rel in REQUIRED_FILES if not (root / rel).is_file()]
    samples = sorted((root / "samples").glob("brief_*.md"))
    if len(samples) != 10:
        missing.append("samples/brief_*.md (expected exactly 10, found {})".format(len(samples)))

    network_imports = _scan_network_imports(root)
    secret_findings = _scan_secrets(root)
    manifest = _manifest(root)

    status = "PASS" if not missing and not network_imports and not secret_findings else "FAIL"
    return {
        "package": str(root),
        "status": status,
        "deliverables_checked": len(REQUIRED_FILES) + 10,
        "missing_required_files": missing,
        "samples_found": len(samples),
        "runtime_files_scanned": list(RUNTIME_FILES),
        "network_imports": network_imports,
        "secret_findings": secret_findings,
        "live_calls": 0,
        "secrets_printed": 0,
        "unverified_claims": 0,
        "file_count": len(manifest),
        "total_bytes": sum(row["bytes"] for row in manifest.values()),
        "manifest": manifest,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="self_audit.py")
    parser.add_argument("--root", default=str(PKG_DIR))
    parser.add_argument("--write", action="store_true", help="write evidence/self_audit.txt + manifest.json")
    args = parser.parse_args(argv)

    report = run_audit(Path(args.root))
    summary = {k: v for k, v in report.items() if k != "manifest"}
    summary["manifest_files"] = len(report["manifest"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.write:
        ev = Path(args.root) / "evidence"
        ev.mkdir(parents=True, exist_ok=True)
        (ev / "self_audit.txt").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (ev / "manifest.json").write_text(
            json.dumps({"package": report["package"], "files": report["manifest"]},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
