"""ZERO-network proof suite — three independent layers.

1. The autouse socket tripwire (conftest) fails ANY test that attempts a
   socket / DNS / connection. Every test in this module asserts 0 attempts,
   except the positive control that proves the tripwire is armed.
2. A subprocess run proving that importing the whole package and dry-running
   every adapter adds NO network-capable module to the interpreter
   (socket, ssl, http.client, urllib.request, requests, httpx, aiohttp, ...).
3. An AST source scan proving no package module imports a network module at
   module level, and that function-level network imports exist ONLY in
   executors.py — the single documented lazy boundary.

Anti-vacuity: both the tripwire and the AST scanner have positive controls
that prove they can detect a violation.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from base import CHANNELS, PublishRefused, adapter_registry
from conftest import NetworkBlocked
from helpers import FakeExecutor, RecordingSleeper, make_request, write_approval

PKG_DIR = Path(__file__).resolve().parents[1]

# Modules that can actually perform network I/O. urllib.parse is excluded:
# it is pure string parsing and is pulled in by pathlib at startup.
NETWORK_MODULES = (
    "socket",
    "ssl",
    "http.client",
    "urllib.request",
    "urllib.error",
    "requests",
    "httpx",
    "aiohttp",
)
NETWORK_ROOTS = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "ftplib",
    "smtplib",
    "telnetlib",
    "xmlrpc",
    "nntplib",
    "poplib",
    "imaplib",
}


# ---------------------------------------------------------------------------
# layer 1: runtime matrix under the tripwire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNELS)
def test_dark_default_publish_zero_network(channel, tmp_path):
    adapter = adapter_registry()[channel]()  # dry_run=True default
    receipt = adapter.publish(make_request(channel, tmp_path))
    assert receipt.dry_run is True
    assert receipt.steps_executed == 0


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("scenario", ["no_keys", "env_only", "approval_only"])
def test_gate_closed_live_attempts_zero_network(channel, scenario, tmp_path):
    cls = adapter_registry()[channel]
    env = {}
    if scenario == "env_only":
        env = {"PUBLISH_LIVE": channel}
    elif scenario == "approval_only":
        write_approval(tmp_path, channel)
    fake = FakeExecutor()
    adapter = cls(dry_run=False, approvals_dir=tmp_path, env=env, executor=fake)
    with pytest.raises(PublishRefused):
        adapter.publish(make_request(channel, tmp_path))
    assert fake.calls == []


@pytest.mark.parametrize("channel", CHANNELS)
def test_gate_open_via_fake_executor_zero_real_network(channel, tmp_path):
    from helpers import env_for

    write_approval(tmp_path, channel)
    env = env_for(channel, PUBLISH_LIVE=channel)
    fake = FakeExecutor(responses=_open_responses(channel))
    adapter = adapter_registry()[channel](
        dry_run=False, approvals_dir=tmp_path, env=env, executor=fake, sleeper=RecordingSleeper()
    )
    receipt = adapter.publish(make_request(channel, tmp_path))
    assert receipt.dry_run is False
    assert receipt.accepted is True
    assert len(fake.calls) == receipt.steps_executed


def _open_responses(channel):
    from executors import HttpResponse

    if channel == "instagram":
        return [{"id": "container-1"}, {"status_code": "FINISHED"}, {"id": "post-1"}]
    if channel == "youtube":
        return [
            HttpResponse(status_code=200, json={"id": "s"}, headers={"Location": "https://upload.invalid/1"}),
            {"id": "yt-1"},
        ]
    if channel == "facebook":
        return [{"id": "fb-1"}]
    return [{"data": {"id": "m1"}}, {"data": {"id": "m1"}}, {"data": {"id": "m1"}}, {"data": {"id": "t1"}}]


@pytest.mark.expect_net(1)
def test_positive_control_tripwire_is_armed():
    """Anti-vacuity: the tripwire genuinely intercepts a socket attempt."""
    with pytest.raises(NetworkBlocked):
        socket.socket()


def test_no_network_modules_loaded_by_dark_run_in_subprocess():
    script = "\n".join(
        [
            "import json, sys",
            "sys.path.insert(0, " + repr(str(PKG_DIR)) + ")",
            "baseline = set(sys.modules)",
            "import base, instagram_graph, youtube_shorts, facebook_page, x_api",
            "from base import PublishRequest",
            "registry = base.adapter_registry()",
            "dry = []",
            "for ch in base.CHANNELS:",
            "    adapter = registry[ch]()",
            "    receipt = adapter.publish(PublishRequest(channel=ch, job_id='j1', title='t', caption='c', video_path='/tmp/render.mp4'))",
            "    dry.append(receipt.dry_run)",
            "added = sorted(set(sys.modules) - baseline)",
            "network = sorted(m for m in added if m in " + repr(list(NETWORK_MODULES)) + ")",
            "print(json.dumps({'dry_run': dry, 'network_added': network}))",
        ]
    )
    env = {key: value for key, value in os.environ.items() if key != "PUBLISH_LIVE"}
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(PKG_DIR),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["dry_run"] == [True, True, True, True]
    assert payload["network_added"] == []


# ---------------------------------------------------------------------------
# layer 3: AST source scan (with positive control)
# ---------------------------------------------------------------------------


def _import_roots(nodes) -> set:
    roots = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = str(name).split(".")[0]
            if root:
                roots.add(root)
    return roots


def _module_level_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]


def _all_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]


def test_ast_no_module_level_network_imports_anywhere():
    for path in sorted(PKG_DIR.glob("*.py")):
        offenders = _import_roots(_module_level_imports(path)) & NETWORK_ROOTS
        assert not offenders, path.name + " imports network modules at module level: " + repr(offenders)


def test_ast_function_level_network_imports_only_in_executors():
    for path in sorted(PKG_DIR.glob("*.py")):
        if path.name == "executors.py":
            continue
        offenders = _import_roots(_all_imports(path)) & NETWORK_ROOTS
        assert not offenders, path.name + " imports network modules: " + repr(offenders)


def test_ast_executors_network_imports_are_function_local():
    path = PKG_DIR / "executors.py"
    assert not (_import_roots(_module_level_imports(path)) & NETWORK_ROOTS)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    local_network_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    for alias in sub.names:
                        if alias.name.split(".")[0] in NETWORK_ROOTS:
                            local_network_imports.append(alias.name)
    assert "urllib.request" in local_network_imports
    assert "urllib.error" in local_network_imports


def test_ast_scanner_detects_synthetic_violations(tmp_path):
    """Anti-vacuity for the scanner itself."""
    bad = tmp_path / "bad_module.py"
    bad.write_text("import socket\n\n\ndef f():\n    import urllib.request\n", encoding="utf-8")
    assert _import_roots(_module_level_imports(bad)) & NETWORK_ROOTS
    assert _import_roots(_all_imports(bad)) & NETWORK_ROOTS


def test_no_channel_approval_files_are_shipped():
    approvals = PKG_DIR / "approvals"
    for channel in CHANNELS:
        assert not (approvals / (channel + ".json")).exists(), channel
    assert (approvals / "example.approval.json").exists()
