"""Make ``import app`` / ``import db`` work from any pytest invocation cwd."""

import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[1]
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))
