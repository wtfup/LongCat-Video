"""Pytest bootstrap: make the package importable regardless of invocation style.

Keeps ``python -m pytest`` working even without the pyproject ``pythonpath`` ini.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
