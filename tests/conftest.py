"""Test bootstrap: set_lineup.py reads config.json at import time; on a fresh
clone only config.example.json exists, so provision it before tests import.

The autouse fixture then pins the config values the tests assert against to
the canonical config.example.json values, so a personalized config.json
(different min_swap_gain, extra flex slots, ...) cannot break the suite."""
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

cfg = ROOT / "config.json"
if not cfg.exists():
    shutil.copy(ROOT / "config.example.json", cfg)

EXAMPLE = json.loads((ROOT / "config.example.json").read_text())


@pytest.fixture(autouse=True)
def canonical_config(monkeypatch):
    import set_lineup as sl
    pinned = {**sl.CFG, **{k: EXAMPLE[k] for k in (
        "min_swap_gain", "lineup_json_max_age_hours",
        "slot_eligibility", "bad_statuses")}}
    monkeypatch.setattr(sl, "CFG", pinned)
    monkeypatch.setattr(sl, "BAD", set(EXAMPLE["bad_statuses"]))
