#!/usr/bin/env python
"""W2 console self-audit.

Runs a complete audit of the console package and prints a PASS/FAIL report.
Exits non-zero if anything fails. The BRIEF contract for W2 is:

    Done = self-audit prints 0 failures + tests green + evidence written.

Checks
  1. every headline deliverable exists at its exact absolute path
  2. README.md ends with a '## 5. Self-audit' block
  3. static assets contain zero external references (no CDN, no webfonts)
  4. secret scan across all authored text files (0 hits expected)
  5. the app surface (app.py, db.py) imports no network modules
  6. the full pytest suite passes on a fresh invocation
  7. the zero-network test passes in isolation
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent

DELIVERABLES = [
    "app.py",
    "db.py",
    "static/index.html",
    "static/styles.css",
    "static/app.js",
    "tests/conftest.py",
    "tests/test_console.py",
    "requirements.txt",
    "README.md",
    "self_audit.py",
    "evidence/run_evidence.py",
]

TEXT_EXTS = {".py", ".md", ".html", ".css", ".js", ".txt", ".json",
             ".yaml", ".yml", ".toml", ".sh", ".ini", ".cfg"}

SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_style_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("assigned_password", re.compile(r"(?i)password\s*[=:]\s*['\"][^'\"]{4,}")),
    ("bearer_literal", re.compile(r"(?i)authorization\s*[:=]\s*['\"]bearer\s+\S{10,}")),
]

NETWORK_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(?:requests|urllib|http\.client|socket|aiohttp|httpx)\b",
    re.M,
)

EXTERNAL_REF_NEEDLES = ("http://", "https://", "//cdn.", "fonts.googleapis", "integrity=")

FAILURES: list = []


def report(label: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({extra})" if extra else ""))
    if not ok:
        FAILURES.append(label)


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTS and path.is_file()


def main() -> int:
    print("== W2 console self-audit ==")
    print(f"package: {PKG}\n")

    # 1 -------------------------------------------------------- deliverables
    missing = [rel for rel in DELIVERABLES if not (PKG / rel).exists()]
    report("1 deliverables exist at exact paths", not missing,
           f"{len(DELIVERABLES)} files" + (f" — MISSING: {missing}" if missing else ""))

    # 2 ------------------------------------------------------------ README
    readme = PKG / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        headings = [h.strip() for h in re.findall(r"^## .*$", text, re.M)]
        report("2 README ends with '## 5. Self-audit' section",
               bool(headings) and headings[-1] == "## 5. Self-audit",
               headings[-1] if headings else "no headings found")
    else:
        report("2 README ends with '## 5. Self-audit' section", False, "README.md missing")

    # 3 ------------------------------------------------------ static refs
    external_hits = []
    for name in ("index.html", "styles.css", "app.js"):
        f = PKG / "static" / name
        if f.exists():
            body = f.read_text(encoding="utf-8")
            for needle in EXTERNAL_REF_NEEDLES:
                if needle in body:
                    external_hits.append(f"{name}:{needle}")
    report("3 static assets have zero external references", not external_hits,
           "clean" if not external_hits else str(external_hits))

    # 4 --------------------------------------------------------- secret scan
    secret_hits = []
    scanned = 0
    for path in sorted(PKG.rglob("*")):
        if ".git" in path.parts or "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        if not is_text(path):
            continue
        scanned += 1
        body = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(body):
                secret_hits.append(f"{path.relative_to(PKG)}:{label}")
    report("4 secret scan (0 expected)", not secret_hits,
           f"scanned {scanned} text files" + (f" — HITS: {secret_hits}" if secret_hits else ""))

    # 5 --------------------------------------------------- network imports
    net_hits = []
    for name in ("app.py", "db.py"):
        f = PKG / name
        if f.exists() and NETWORK_IMPORT_RE.search(f.read_text(encoding="utf-8")):
            net_hits.append(name)
    report("5 app surface imports no network modules", not net_hits,
           "app.py + db.py clean" if not net_hits else str(net_hits))

    # 6 ----------------------------------------------------------- pytest
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=str(PKG), capture_output=True, text=True,
    )
    tail = (proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else ""
    report("6 full pytest suite green (fresh invocation)", proc.returncode == 0, summary)
    if proc.returncode != 0:
        print(proc.stdout[-2000:])

    # 7 --------------------------------------------- zero-network test alone
    proc2 = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         "tests/test_console.py::test_zero_network_calls"],
        cwd=str(PKG), capture_output=True, text=True,
    )
    tail2 = (proc2.stdout or "").strip().splitlines()
    report("7 zero-network proof passes in isolation", proc2.returncode == 0,
           tail2[-1] if tail2 else "")

    print()
    print(f"SELF-AUDIT FAILURES: {len(FAILURES)}")
    for label in FAILURES:
        print(f"  - {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
