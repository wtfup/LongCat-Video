"""The package self-audit tool must pass on this package."""
from __future__ import annotations

from pathlib import Path

import self_audit

PKG = Path(__file__).resolve().parents[1]


def test_audit_reports_clean_package():
    report = self_audit.run_audit(PKG)
    assert report["status"] == "PASS", report
    assert report["missing_required_files"] == []
    assert report["network_imports"] == []
    assert report["secret_findings"] == []
    assert report["live_calls"] == 0


def test_audit_manifest_covers_all_deliverables():
    report = self_audit.run_audit(PKG)
    manifest = report["manifest"]
    for required in ("brain.py", "calendar.py", "providers.py", "pillars.yaml", "README.md"):
        assert required in manifest, required
    for row in manifest.values():
        assert len(row["sha256"]) == 64
        assert row["bytes"] > 0
