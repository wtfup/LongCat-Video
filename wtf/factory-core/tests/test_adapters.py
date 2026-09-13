"""Adapter tests: dark defaults, live refusal, deterministic stubs, no net imports."""
import ast
import sys
from pathlib import Path

import pytest

import factory_core
from factory_core.adapters import (
    ADAPTER_SPEC,
    DARK,
    LIVE_ENABLED,
    NETWORK_ENABLED,
    REQUIRED_STAGES,
    LiveAccessRefused,
    QaGateAdapter,
    RenderAdapter,
    StageResult,
    default_adapters,
)
from factory_core.jobqueue import Job, utc_now

PKG_DIR = Path(factory_core.__file__).resolve().parent

# Modules that would enable network egress if imported by factory code.
NETWORK_MODULES = {
    "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "telnetlib",
    "xmlrpc", "requests", "httpx", "aiohttp", "websockets", "boto3", "botocore",
    "paramiko", "grpc",
}


def _make_job(job_id="job_test_0001"):
    return Job(id=job_id, created_at=utc_now(), brand="WTF Gyms",
               pillar="fitness-transformation", language="hindi")


def test_module_level_dark_flags():
    assert DARK is True
    assert LIVE_ENABLED is False
    assert NETWORK_ENABLED is False
    assert REQUIRED_STAGES == ("voice", "render", "gate", "publish")


def test_every_adapter_is_dark_by_default():
    for name, adapter in default_adapters().items():
        assert adapter.dark is True, name
        assert adapter.network_enabled is False, name
        assert name in ADAPTER_SPEC


def test_every_adapter_refuses_live_construction():
    for name, cls in ADAPTER_SPEC.items():
        with pytest.raises(LiveAccessRefused):
            cls(dark=False)
        adapter = cls(dark=True)
        assert adapter.dark is True


def test_run_requires_a_job():
    for cls in ADAPTER_SPEC.values():
        with pytest.raises(TypeError):
            cls().run({"not": "a job"})


def test_stage_result_rejects_bad_mode_and_output():
    with pytest.raises(ValueError):
        StageResult(stage="x", ok=True, mode="hybrid")
    with pytest.raises(ValueError):
        StageResult(stage="x", ok=True, output=["not", "a", "dict"])


def test_voice_and_render_stubs_are_offline_and_write_nothing():
    job = _make_job()
    voice = ADAPTER_SPEC["voice"]().run(job)
    assert voice.ok and voice.mode == "dark" and voice.output["simulated"] is True
    assert voice.output["audio_path"] is None

    render = ADAPTER_SPEC["render"]().run(job)
    assert render.ok and render.mode == "dark"
    render_path = Path(render.output["render_path"])
    assert render_path.name == f"{job.id}.mp4"
    assert "wtf" in str(render_path)
    assert render.output["exists"] is False
    assert not render_path.exists()  # stub must never create files


def test_gate_score_is_deterministic_and_ranged():
    job = _make_job()
    gate = QaGateAdapter()
    first = gate.run(job)
    second = gate.run(job)
    assert first.output["score"] == second.output["score"]
    assert 0.60 <= first.output["score"] <= 0.99
    assert first.output["checks"]["integrity"] == "stub"
    other = gate.run(_make_job("job_test_0002"))
    assert other.output["score"] != first.output["score"]


def test_publisher_stub_makes_no_call():
    job = _make_job()
    result = ADAPTER_SPEC["publish"]().run(job)
    assert result.ok and result.mode == "dark"
    assert result.output["receipt"] == f"dark-publish:{job.id}"
    assert result.output["simulated"] is True


def test_no_network_imports_anywhere_in_factory_core():
    """AST scan: factory_core must not even import network-capable modules."""
    offenders = {}
    for source in sorted(PKG_DIR.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in NETWORK_MODULES:
                        offenders.setdefault(source.name, set()).add(top)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    if top in NETWORK_MODULES:
                        offenders.setdefault(source.name, set()).add(top)
    assert offenders == {}, f"network-capable imports found: {offenders}"


def test_every_import_is_stdlib_or_intra_package():
    """Positive provenance check: no third-party imports at runtime."""
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    allow_fallback = {
        "__future__", "argparse", "ast", "dataclasses", "datetime", "hashlib",
        "json", "os", "pathlib", "sqlite3", "sys", "time", "typing", "uuid",
    }
    unexpected = {}
    for source in sorted(PKG_DIR.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in stdlib and top not in allow_fallback:
                        unexpected.setdefault(source.name, set()).add(top)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top = node.module.split(".")[0]
                if top not in stdlib and top not in allow_fallback:
                    unexpected.setdefault(source.name, set()).add(top)
    assert unexpected == {}, f"unexpected imports: {unexpected}"
