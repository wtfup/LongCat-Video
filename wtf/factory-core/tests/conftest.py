"""Shared fixtures for the factory-core test suite."""
import pytest

from factory_core.jobqueue import FactoryDB
from factory_core.pipeline import Pipeline


@pytest.fixture()
def db(tmp_path):
    handle = FactoryDB(tmp_path / "factory.db")
    yield handle
    handle.close()


@pytest.fixture()
def pipeline(db):
    captured = []
    inst = Pipeline(db, log=captured.append)
    setattr(inst, "captured_log", captured)  # test-side convenience
    return inst
