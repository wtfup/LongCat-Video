#!/usr/bin/env python3
"""W3 Render Kit — self-audit tool.

One canonical implementation used by both the pytest suite and
make_evidence.sh. Two jobs:

1. ``secret_scan`` — scan every file in the package for credential-shaped
   strings. Token regexes are ASSEMBLED FROM FRAGMENTS (and this file is
   excluded from its own scan) so that a scanner never flags its own pattern
   table; see the module docstring in tests/test_package_contract.py.
2. ``audit`` — machine-readable audit JSON: deliverable presence + hashes,
   writes count, test inventory, evidence inventory, secret findings.

Stdlib-only. Makes zero network calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
_EXCLUDE_FILES = {Path(__file__).name}  # never scan its own pattern table

DELIVERABLES = [
    "setup_l40s.sh",
    "download_weights.sh",
    "run_avatar.py",
    "benchmark.py",
    "models.md",
    "RUNBOOK.md",
]

MAIN_DOCS = ["README.md", "models.md", "RUNBOOK.md"]


def _frag(*parts: str) -> str:
    return "".join(parts)


def secret_patterns():
    # Each full pattern is assembled at runtime from harmless fragments.
    return [
        ("aws-access-key-id", _frag("AK", "IA") + r"[0-9A-Z]{16}"),
        ("aws-session-key-id", _frag("AS", "IA") + r"[0-9A-Z]{16}"),
        ("openai-style-key", _frag("s", "k-") + r"[A-Za-z0-9_-]{20,}"),
        ("github-pat", _frag("gh", "p_") + r"[A-Za-z0-9]{30,}"),
        ("github-pat-fine", _frag("github", "_pat_") + r"[A-Za-z0-9_]{20,}"),
        ("hf-token", _frag("h", "f_") + r"[A-Za-z0-9]{20,}"),
        ("slack-token", _frag("xox", "[bp]-") + r"[A-Za-z0-9-]{10,}"),
        ("google-api-key", _frag("AI", "za") + r"[0-9A-Za-z_-]{35}"),
        ("pem-private-key", _frag("-----BEGIN ", "[A-Z ]*", "PRIVATE KEY-----")),
    ]


def iter_files(root, skip_self_scan=True):
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if skip_self_scan and path.name in _EXCLUDE_FILES and path.parent == root:
            continue
        yield path


def secret_scan(root):
    findings = []
    compiled = [(kind, re.compile(pattern)) for kind, pattern in secret_patterns()]
    # `iter_files` skips this file's own pattern table so the scanner never
    # flags itself; every other file in the package is scanned.
    for path in iter_files(root, skip_self_scan=True):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for kind, rx in compiled:
                if rx.search(line):
                    findings.append({
                        "file": str(path.relative_to(root)),
                        "line": lineno,
                        "kind": kind,
                    })
    return findings


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kind(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    parts = rel.parts
    if parts[0] == "tests":
        return "tests"
    if parts[0] == "evidence":
        return "evidence"
    if path.suffix == ".sh":
        return "scripts"
    if path.suffix in {".md", ".rst"}:
        return "docs"
    if path.suffix == ".py":
        return "python"
    return "other"


def audit(root):
    root = Path(root)
    all_files = [p for p in iter_files(root, skip_self_scan=False)]
    source = [p for p in all_files if "evidence" not in p.relative_to(root).parts]
    evidence = [p for p in all_files if "evidence" in p.relative_to(root).parts]
    by_kind = {}
    for path in source:
        by_kind.setdefault(_kind(path, root), []).append(str(path.relative_to(root)))
    deliverables = {}
    for name in DELIVERABLES:
        target = root / name
        deliverables[name] = {
            "exists": target.is_file(),
            "bytes": target.stat().st_size if target.is_file() else None,
            "sha256": sha256_file(target) if target.is_file() else None,
        }
    docs_self_audit = {}
    for name in MAIN_DOCS:
        target = root / name
        text = target.read_text(encoding="utf-8") if target.is_file() else ""
        docs_self_audit[name] = {
            "exists": target.is_file(),
            "has_self_audit_heading": "## 5. Self-audit" in text,
        }
    findings = secret_scan(root)
    return {
        "root": str(root),
        "writes": {
            "tracked_files": len(source),
            "evidence_files": len(evidence),
            "by_kind": {k: len(v) for k, v in sorted(by_kind.items())},
            "file_list": {k: sorted(v) for k, v in sorted(by_kind.items())},
            "evidence_list": sorted(str(p.relative_to(root)) for p in evidence),
        },
        "deliverables": deliverables,
        "docs_self_audit": docs_self_audit,
        "secret_scan": {
            "scope": "all files under root except __pycache__/.pytest_cache and self_audit.py",
            "findings": findings,
            "clean": not findings,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render Kit self-audit.")
    parser.add_argument("--root", default=str(PKG_ROOT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = audit(Path(args.root))
    print(json.dumps(result, indent=2))
    if not result["secret_scan"]["clean"]:
        return 1
    if not all(d["exists"] for d in result["deliverables"].values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
