"""Unit tests for the pure decision logic in set_lineup.py.

No browser required: these cover parse_float, eligibility, startability,
the greedy optimizer (plan_swaps), and the lineup.json override loader —
i.e. every guardrail that decides WHAT to write, independent of the DOM.
"""
import json
import time

import pytest

import set_lineup as sl


def P(name, slot, primary=None, status="", bye=False, proj=0.0, locked=False):
    return {"name": name, "slot": slot, "primary": primary or slot,
            "status": status, "bye": bye, "proj": proj, "locked": locked}


# ---------- parse_float ----------

@pytest.mark.parametrize("raw,expected", [
    ("12.4", 12.4),
    ("-3.2", -3.2),
    ("7", 7.0),
    ("Proj 9.81 pts", 9.81),
    ("", 0.0),
    (None, 0.0),
    ("no numbers here", 0.0),
])
def test_parse_float(raw, expected):
    assert sl.parse_float(raw) == expected


# ---------- eligibility / startability ----------

def test_rb_eligible_for_rb_and_flex_only():
    rb = P("Back", "BN", primary="RB")
    assert sl.eligible(rb, "RB")
    assert sl.eligible(rb, "W/R/T")
    assert not sl.eligible(rb, "QB")
    assert not sl.eligible(rb, "WR")
    assert not sl.eligible(rb, "TE")
    assert not sl.eligible(rb, "BN")


def test_qb_never_fills_flex():
    qb = P("Quarterback", "BN", primary="QB")
    assert sl.eligible(qb, "QB")
    assert not sl.eligible(qb, "W/R/T")


def test_unknown_primary_never_eligible():
    mystery = P("Mystery", "BN", primary=None)
    mystery["primary"] = None
    assert not sl.eligible(mystery, "RB")


def test_startable_rejects_bye_and_bad_statuses():
    assert sl.startable(P("Ok", "RB"))
    assert sl.startable(P("Questionable", "RB", status="Q"))   # Q is startable
    assert sl.startable(P("Doubtful", "RB", status="D"))       # D is startable
    assert not sl.startable(P("OnBye", "RB", bye=True))
    for bad in ("O", "IR", "SUSP", "NA", "PUP", "NFI"):
        assert not sl.startable(P("Hurt", "RB", status=bad)), bad


# ---------- plan_swaps: pass 1 (unstartable starters) ----------

def test_bye_starter_replaced_by_best_eligible_bench():
    roster = [
        P("ByeRB", "RB", primary="RB", bye=True, proj=14.0),
        P("BenchRB1", "BN", primary="RB", proj=8.0),
        P("BenchRB2", "BN", primary="RB", proj=11.0),
    ]
    swaps = sl.plan_swaps(roster)
    assert swaps == [("ByeRB", "BenchRB2", "RB", -3.0)]


def test_out_starter_replaced_even_at_projection_loss():
    roster = [
        P("OutWR", "WR", primary="WR", status="O", proj=15.0),
        P("BenchWR", "BN", primary="WR", proj=4.0),
    ]
    swaps = sl.plan_swaps(roster)
    assert swaps == [("OutWR", "BenchWR", "WR", -11.0)]


def test_no_eligible_bench_means_no_swap():
    roster = [
        P("OutQB", "QB", primary="QB", status="O", proj=20.0),
        P("BenchRB", "BN", primary="RB", proj=12.0),   # RB can't fill QB
    ]
    assert sl.plan_swaps(roster) == []


def test_bye_bench_never_started():
    roster = [
        P("OutRB", "RB", primary="RB", status="O", proj=10.0),
        P("ByeBenchRB", "BN", primary="RB", bye=True, proj=18.0),
    ]
    assert sl.plan_swaps(roster) == []


def test_locked_bench_never_used():
    roster = [
        P("OutRB", "RB", primary="RB", status="O", proj=10.0),
        P("LockedBenchRB", "BN", primary="RB", proj=18.0, locked=True),
    ]
    assert sl.plan_swaps(roster) == []


def test_locked_unstartable_starter_swap_still_planned():
    # Per plan §0/§9: the attempt on a locked slot is made and the execution
    # layer reports it as skipped (locked/timeout) — planning must not hide it.
    roster = [
        P("LockedOutRB", "RB", primary="RB", status="O", proj=2.0, locked=True),
        P("BenchRB", "BN", primary="RB", proj=18.0),
    ]
    assert sl.plan_swaps(roster) == [("LockedOutRB", "BenchRB", "RB", 16.0)]


def test_locked_healthy_starter_not_upgraded():
    # Pass 2 explicitly skips locked starters — no churn attempt on a locked slot.
    roster = [
        P("LockedRB", "RB", primary="RB", proj=5.0, locked=True),
        P("BenchRB", "BN", primary="RB", proj=18.0),
    ]
    assert sl.plan_swaps(roster) == []


# ---------- plan_swaps: pass 2 (projection upgrades) ----------

def test_upgrade_above_threshold():
    roster = [
        P("WeakRB", "RB", primary="RB", proj=6.0),
        P("StrongBenchRB", "BN", primary="RB", proj=9.5),
    ]
    swaps = sl.plan_swaps(roster)
    assert swaps == [("WeakRB", "StrongBenchRB", "RB", 3.5)]


def test_min_gain_threshold_blocks_churn():
    # min_swap_gain is 1.0 in config: a +0.5 edge must NOT trigger a swap
    roster = [
        P("StarterRB", "RB", primary="RB", proj=9.0),
        P("BenchRB", "BN", primary="RB", proj=9.5),
    ]
    assert sl.plan_swaps(roster) == []


def test_gain_exactly_at_threshold_swaps():
    roster = [
        P("StarterRB", "RB", primary="RB", proj=9.0),
        P("BenchRB", "BN", primary="RB", proj=10.0),
    ]
    swaps = sl.plan_swaps(roster)
    assert swaps == [("StarterRB", "BenchRB", "RB", 1.0)]


def test_bench_player_used_once_across_passes():
    # One great bench RB, two weak starters: he can only replace one of them.
    roster = [
        P("OutRB", "RB", primary="RB", status="O", proj=0.0),
        P("WeakFlex", "W/R/T", primary="WR", proj=3.0),
        P("GreatBenchRB", "BN", primary="RB", proj=16.0),
    ]
    swaps = sl.plan_swaps(roster)
    assert len(swaps) == 1
    assert swaps[0] == ("OutRB", "GreatBenchRB", "RB", 16.0)


def test_optimal_lineup_yields_no_swaps():
    roster = [
        P("QB1", "QB", primary="QB", proj=20.0),
        P("RB1", "RB", primary="RB", proj=15.0),
        P("WR1", "WR", primary="WR", proj=14.0),
        P("BenchRB", "BN", primary="RB", proj=7.0),
        P("BenchWR", "BN", primary="WR", proj=6.0),
    ]
    assert sl.plan_swaps(roster) == []


def test_flex_upgrade_from_wr_bench():
    roster = [
        P("FlexTE", "W/R/T", primary="TE", proj=5.0),
        P("BenchWR", "BN", primary="WR", proj=12.0),
    ]
    swaps = sl.plan_swaps(roster)
    assert swaps == [("FlexTE", "BenchWR", "W/R/T", 7.0)]


def test_ir_slot_players_are_not_starters_or_bench():
    # A player in the IR slot must never be swapped in or out by the optimizer.
    roster = [
        P("IRGuy", "IR", primary="RB", status="IR", proj=22.0),
        P("WeakRB", "RB", primary="RB", proj=5.0),
    ]
    assert sl.plan_swaps(roster) == []


# ---------- load_override (lineup.json freshness) ----------

def test_override_fresh_file_returns_swaps(tmp_path, monkeypatch):
    f = tmp_path / "lineup.json"
    f.write_text(json.dumps({"swaps": [{"out": "A", "in": "B"}]}))
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_override() == [{"out": "A", "in": "B"}]


def test_override_stale_file_ignored(tmp_path, monkeypatch):
    f = tmp_path / "lineup.json"
    f.write_text(json.dumps({"swaps": [{"out": "A", "in": "B"}]}))
    stale = time.time() - (sl.CFG["lineup_json_max_age_hours"] + 1) * 3600
    import os
    os.utime(f, (stale, stale))
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_override() is None


def test_override_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_override() is None


def test_override_malformed_json_returns_none(tmp_path, monkeypatch):
    (tmp_path / "lineup.json").write_text("{not json")
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_override() is None


def test_override_empty_swaps_returns_none(tmp_path, monkeypatch):
    (tmp_path / "lineup.json").write_text(json.dumps({"swaps": []}))
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_override() is None
