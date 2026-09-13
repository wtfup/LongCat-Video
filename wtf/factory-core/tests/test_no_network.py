"""Zero-network proof: the complete lifecycle and CLI demo under a socket guard.

If any code path in factory-core attempts a network call, the guard raises and
the test fails. This is the executable version of the 'dark by default' claim.
"""
import socket
import urllib.request
import http.client

import pytest

from factory_core.adapters import DEMO_FAILING_RENDER, default_adapters
from factory_core.cli import cmd_demo, main
from factory_core.jobqueue import FactoryDB
from factory_core.pipeline import Pipeline


@pytest.fixture()
def no_network(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network call attempted by factory-core (DARK VIOLATION)")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(socket, "gethostbyname", boom)
    monkeypatch.setattr(http.client, "HTTPConnection", boom)
    monkeypatch.setattr(http.client, "HTTPSConnection", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def test_full_lifecycle_with_socket_guard(tmp_path, no_network):
    db = FactoryDB(tmp_path / "factory.db")
    try:
        pipeline = Pipeline(db)
        j1 = db.add_job(brand="WTF Gyms", pillar="fitness-transformation", language="hindi", title="a")
        j2 = db.add_job(brand="EVRYDAY", pillar="business", language="english", title="b")
        assert [j.status for j in pipeline.drain()] == ["gated", "gated"]
        pipeline.approve(j1.id)
        pipeline.reject(j2.id, notes="no")
        assert [j.status for j in pipeline.publish_approved()] == ["published"]

        # failure path under the guard too
        failing = Pipeline(db, adapters={**default_adapters(), "render": DEMO_FAILING_RENDER()})
        j3 = db.add_job(brand="Reboot", pillar="ai-systems", language="hinglish", title="c")
        assert failing.run_job(j3.id).status == "failed"
        failing.requeue(j3.id)
        assert pipeline.run_job(j3.id).status == "gated"
    finally:
        db.close()


def test_cli_demo_under_socket_guard(tmp_path, no_network, capsys):
    class Args:
        dir = str(tmp_path / "demo-out")

    assert cmd_demo(Args) == 0
    capsys.readouterr()
    log = (tmp_path / "demo-out" / "demo.log").read_text(encoding="utf-8")
    assert "STATUS: SUCCESS" in log


def test_cli_run_and_publish_under_socket_guard(tmp_path, no_network, capsys):
    db_path = str(tmp_path / "cli.db")
    assert main(["--db", db_path, "add", "--brand", "WTF Gyms", "--pillar", "fitness",
                 "--language", "hindi", "--title", "guarded"]) == 0
    job_id = capsys.readouterr().out.strip()
    assert main(["--db", db_path, "run"]) == 0
    assert main(["--db", db_path, "approve", job_id]) == 0
    assert main(["--db", db_path, "publish"]) == 0
    out = capsys.readouterr().out
    assert job_id in out and "published" in out
