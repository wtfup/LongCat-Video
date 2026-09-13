"""Shared test fixtures for the W4 Content Brain package.

Key guarantees enforced here:
- The package root is importable regardless of pytest invocation cwd.
- Every test runs with sockets hard-disabled: any attempted network I/O
  raises immediately. This is how "0 live calls" is *proved*, not claimed.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))


def _pin_local_calendar() -> None:
    """The BRIEF mandates the module name ``calendar.py``; Python's stdlib also
    ships a ``calendar`` module. Depending on import order (pytest may import
    the stdlib one during startup before this conftest runs), ``import calendar``
    in the test modules could resolve to the wrong module when the suite is
    invoked from outside the package directory.

    Guarantee: ``calendar`` names the package module for the whole test session.
    """
    existing = sys.modules.get("calendar")
    if existing is not None and not hasattr(existing, "plan_days"):
        del sys.modules["calendar"]
    import calendar as package_calendar  # noqa: F401 - must resolve to PKG_ROOT/calendar.py

    assert hasattr(package_calendar, "plan_days"), (
        "local calendar.py did not shadow the stdlib module; check sys.path"
    )


_pin_local_calendar()

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Autouse guard: fail the test if anything tries to open a socket."""

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "network access attempted during W4 tests - content-brain must be fully offline"
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    yield


@pytest.fixture()
def pkg_root() -> Path:
    return PKG_ROOT


@pytest.fixture()
def sample_date():
    from datetime import date

    return date(2026, 9, 14)
