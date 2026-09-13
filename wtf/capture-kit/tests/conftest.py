"""Make the capture-kit package importable from tests without installation."""
from __future__ import annotations

import pathlib
import sys

PKG_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))
