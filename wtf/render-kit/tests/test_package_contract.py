"""W3 Render Kit — package contract tests.

These enforce the worker contract itself (BRIEF.md row W3):
  * all six headline deliverables exist at the package root
  * shell scripts pass `bash -n` and are executable
  * every main doc ends with a `## 5. Self-audit` block
  * zero credential-shaped strings anywhere in the package (the scan regexes
    are assembled from fragments inside self_audit.py so the scanner never
    flags its own pattern table)
  * the Python deliverables import stdlib only (no torch, no network clients)
  * the shell scripts never invoke git / aws / curl / wget
"""

import ast
import os
import re
import subprocess
from pathlib import Path

import self_audit

PKG = Path(__file__).resolve().parents[1]

PYTHON_DELIVERABLES = ["run_avatar.py", "benchmark.py", "self_audit.py"]
SHELL_DELIVERABLES = ["setup_l40s.sh", "download_weights.sh"]
MAIN_DOCS = ["README.md", "models.md", "RUNBOOK.md"]

ALLOWED_IMPORT_ROOTS = {
    "__future__", "argparse", "datetime", "hashlib", "json", "os", "pathlib",
    "re", "shutil", "signal", "subprocess", "sys", "threading", "time",
    "run_avatar", "benchmark", "self_audit",
}
FORBIDDEN_IMPORT_ROOTS = {"torch", "torchvision", "diffusers", "transformers",
                          "requests", "urllib", "httpx", "socket", "http",
                          "ftplib", "smtplib", "telnetlib", "websockets"}


def test_all_headline_deliverables_exist():
    for name in PYTHON_DELIVERABLES + SHELL_DELIVERABLES + ["models.md", "RUNBOOK.md"]:
        assert (PKG / name).is_file(), "missing deliverable: %s" % name


def test_requirements_txt_pins_test_dependency():
    text = (PKG / "requirements.txt").read_text()
    assert "pytest" in text


def test_evidence_dir_exists():
    assert (PKG / "evidence").is_dir()


def test_shell_scripts_pass_bash_n_and_are_executable():
    for name in SHELL_DELIVERABLES:
        path = PKG / name
        proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, "%s: %s" % (name, proc.stderr)
        assert os.access(path, os.X_OK), "%s is not executable" % name


def test_main_docs_end_with_self_audit_block():
    for name in MAIN_DOCS:
        text = (PKG / name).read_text(encoding="utf-8")
        assert "## 5. Self-audit" in text, "%s lacks a '## 5. Self-audit' block" % name
        headings = [line for line in text.splitlines() if re.match(r"^##\s", line)]
        assert headings[-1].strip() == "## 5. Self-audit", \
            "%s: '## 5. Self-audit' must be the final section (found %r)" % (name, headings[-1])
        tail = text.split("## 5. Self-audit", 1)[1]
        assert len(tail.strip()) > 40, "%s: self-audit block is empty" % name
        for keyword in ("live calls", "secret", "unverified"):
            assert keyword in tail.lower(), "%s self-audit missing %r" % (name, keyword)


def test_no_credential_shaped_strings_in_package():
    findings = self_audit.secret_scan(PKG)
    assert findings == [], findings


def test_python_deliverables_are_stdlib_only():
    for name in PYTHON_DELIVERABLES:
        tree = ast.parse((PKG / name).read_text(), filename=name)
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        bad = roots & FORBIDDEN_IMPORT_ROOTS
        assert not bad, "%s imports forbidden module(s): %s" % (name, bad)
        unexpected = roots - ALLOWED_IMPORT_ROOTS
        assert not unexpected, "%s imports unexpected module(s): %s" % (name, unexpected)


def test_shell_scripts_run_no_git_aws_curl_or_wget():
    forbidden = r"(?:^|[;&|]\s*|sudo\s+)(git|aws|curl|wget)\s"
    for name in SHELL_DELIVERABLES:
        text = (PKG / name).read_text(encoding="utf-8")
        hits = [line for line in text.splitlines()
                if re.search(forbidden, line) and not line.strip().startswith("#")]
        assert hits == [], "%s invokes forbidden command(s): %s" % (name, hits)
        assert "s3://" not in text, "%s references an S3 URI" % name


def test_self_audit_tool_reports_full_delivery():
    result = self_audit.audit(PKG)
    assert result["secret_scan"]["clean"] is True
    assert all(d["exists"] for d in result["deliverables"].values()), result["deliverables"]
    assert all(v["has_self_audit_heading"] for v in result["docs_self_audit"].values())
    assert result["writes"]["tracked_files"] >= 10
