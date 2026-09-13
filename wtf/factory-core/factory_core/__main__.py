"""Allow ``python -m factory_core`` as an alias for ``python -m factory_core.cli``."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
