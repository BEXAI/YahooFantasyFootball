"""Test bootstrap: set_lineup.py reads config.json at import time; on a fresh
clone only config.example.json exists, so provision it before tests import."""
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

cfg = ROOT / "config.json"
if not cfg.exists():
    shutil.copy(ROOT / "config.example.json", cfg)
