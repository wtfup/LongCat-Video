"""Test bootstrap: sys.path wiring + the socket tripwire.

The tripwire replaces socket primitives for EVERY test in this suite
(autouse). Any attempt to open a socket, resolve DNS, or create a connection
records the attempt and raises immediately. At teardown the recorded count is
asserted against the expectation for that test: 0 by default, or
``@pytest.mark.expect_net(n)`` for the positive-control test that proves the
tripwire is actually armed.

This is the test-framework-level kill switch: the publishers package must make
ZERO network calls anywhere in its dark surface, and this fixture fails the run
loudly if that ever stops being true.
"""

from __future__ import annotations

import socket
import ssl
import sys
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for _candidate in (str(PKG_DIR), str(TESTS_DIR)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)


class NetworkBlocked(AssertionError):
    """Raised by the socket tripwire whenever any code attempts network I/O."""


def _make_guard(name: str, attempts: list):
    def _guard(*args, **kwargs):
        attempts.append(name)
        raise NetworkBlocked("network attempt blocked by tripwire: " + name)

    return _guard


@pytest.fixture(autouse=True)
def socket_tripwire(monkeypatch, request):
    attempts: list = []
    monkeypatch.setattr(socket, "socket", _make_guard("socket.socket", attempts))
    monkeypatch.setattr(socket, "create_connection", _make_guard("socket.create_connection", attempts))
    monkeypatch.setattr(socket, "getaddrinfo", _make_guard("socket.getaddrinfo", attempts))
    if hasattr(ssl, "wrap_socket"):
        monkeypatch.setattr(ssl, "wrap_socket", _make_guard("ssl.wrap_socket", attempts))
    yield attempts
    marker = request.node.get_closest_marker("expect_net")
    expected = int(marker.args[0]) if (marker is not None and marker.args) else 0
    assert len(attempts) == expected, (
        "socket tripwire: expected " + str(expected) + " network attempt(s), saw " + repr(attempts)
    )


@pytest.fixture(autouse=True)
def never_really_sleep(monkeypatch):
    """Safety net: no test may burn wall-clock on a poll retry.

    LiveTransport defaults to ``time.sleep``; any test that forgets to inject
    a sleeper would otherwise stall the suite. Poll-interval assertions inject
    RecordingSleeper explicitly.
    """
    import time

    calls: list = []
    monkeypatch.setattr(time, "sleep", lambda seconds: calls.append(seconds))
    return calls
