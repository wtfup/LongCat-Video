"""WTF Avatar Factory - factory-core (W1).

Queue + orchestrator for the avatar content factory (schema v1, dark by default).

- :class:`~factory_core.jobqueue.FactoryDB` - SQLite job queue (shared contract DB)
- :class:`~factory_core.pipeline.Pipeline` - stage orchestrator
- :mod:`factory_core.adapters` - dark stage stubs (zero network)
- :mod:`factory_core.cli` - ``factoryctl`` command line

Zero network access, zero secrets, stdlib only at runtime.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .jobqueue import (  # noqa: E402
    DEFAULT_DB_PATH,
    SCHEMA_SQL,
    STATUSES,
    TRANSITIONS,
    ConcurrentUpdateError,
    FactoryDB,
    InvalidTransition,
    Job,
    JobQueueError,
    NotFoundError,
    ValidationError,
    init_db,
)
from .adapters import default_adapters  # noqa: E402
from .pipeline import Pipeline  # noqa: E402

__all__ = [
    "__version__",
    "DEFAULT_DB_PATH",
    "SCHEMA_SQL",
    "STATUSES",
    "TRANSITIONS",
    "FactoryDB",
    "Job",
    "JobQueueError",
    "NotFoundError",
    "InvalidTransition",
    "ConcurrentUpdateError",
    "ValidationError",
    "init_db",
    "Pipeline",
    "default_adapters",
]
