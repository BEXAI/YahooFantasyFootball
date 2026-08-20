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


# ---------- name normalization ----------

@pytest.mark.parametrize("a,b", [
    ("D.J. Moore", "DJ Moore"),
    ("Odell Beckham Jr.", "Odell Beckham"),
    ("Kenneth Walker III", "kenneth walker"),
    ("  Amon-Ra   St. Brown ", "AmonRa St Brown"),
])
def test_norm_name_equivalences(a, b):
    assert sl.norm_name(a) == sl.norm_name(b)


def test_norm_name_handles_none_and_empty():
    assert sl.norm_name(None) == ""
    assert sl.norm_name("") == ""


def test_roster_index_marks_ambiguous_names():
    roster = [P("John Smith", "RB"), P("John Smith Jr.", "BN", primary="RB")]
    idx = sl.roster_index(roster)
    assert idx[sl.norm_name("John Smith")] is None   # collision → never guessed


# ---------- load_manual_override (lineup.json, mtime freshness) ----------

def test_manual_fresh_file_returns_swaps(tmp_path, monkeypatch):
    (tmp_path / "lineup.json").write_text(json.dumps({"swaps": [{"out": "A", "in": "B"}]}))
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_manual_override() == [{"out": "A", "in": "B"}]


def test_manual_stale_file_ignored(tmp_path, monkeypatch):
    import os
    f = tmp_path / "lineup.json"
    f.write_text(json.dumps({"swaps": [{"out": "A", "in": "B"}]}))
    stale = time.time() - (sl.CFG["lineup_json_max_age_hours"] + 1) * 3600
    os.utime(f, (stale, stale))
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_manual_override() is None


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps({"swaps": []}),
    json.dumps({"swaps": {"out": "A", "in": "B"}}),   # phone-SSH typo: dict not list
    json.dumps({"swaps": ["A,B"]}),
])
def test_manual_bad_content_returns_none(tmp_path, monkeypatch, content):
    (tmp_path / "lineup.json").write_text(content)
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_manual_override() is None


def test_manual_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_manual_override() is None


# ---------- load_advice (advice_remote.json, embedded-timestamp freshness) ----------

def _write_advice(tmp_path, generated_at, swaps=None):
    payload = {"generated_at": generated_at,
               "swaps": swaps if swaps is not None else [{"out": "A", "in": "B"}]}
    (tmp_path / "advice_remote.json").write_text(json.dumps(payload))


def _iso(hours_ago):
    import datetime as dt
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_advice_fresh_timestamp_returns_swaps(tmp_path, monkeypatch):
    _write_advice(tmp_path, _iso(1))
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_advice() == [{"out": "A", "in": "B"}]


def test_advice_stale_timestamp_ignored_even_with_fresh_mtime(tmp_path, monkeypatch):
    # THE trap: git extraction resets mtime to now — freshness must come from
    # the embedded generated_at, or Thursday's advice would run on Sunday.
    _write_advice(tmp_path, _iso(sl.CFG.get("advice_max_age_hours", 6) + 1))
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_advice() is None


def test_advice_naive_timestamp_treated_as_utc(tmp_path, monkeypatch):
    _write_advice(tmp_path, _iso(1).rstrip("Z"))    # no tz marker
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_advice() == [{"out": "A", "in": "B"}]


def test_advice_future_timestamp_rejected(tmp_path, monkeypatch):
    # a hallucinated future generated_at must not make advice permanently
    # "fresh" — that would defeat the freshness mechanism entirely
    _write_advice(tmp_path, _iso(-24 * 30))          # 30 days in the future
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_advice() is None


@pytest.mark.parametrize("generated_at", [None, "not-a-date", 12345])
def test_advice_bad_timestamp_ignored(tmp_path, monkeypatch, generated_at):
    (tmp_path / "advice_remote.json").write_text(
        json.dumps({"generated_at": generated_at, "swaps": [{"out": "A", "in": "B"}]}))
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_advice() is None


def test_advice_empty_swaps_returns_none(tmp_path, monkeypatch):
    _write_advice(tmp_path, _iso(1), swaps=[])
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_advice() is None


def test_advice_missing_or_malformed_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    assert sl.load_advice() is None
    (tmp_path / "advice_remote.json").write_text("[1, 2]")
    assert sl.load_advice() is None


# ---------- build_override_swaps ----------

def _two_player_roster():
    return [
        P("DJ Moore", "WR", primary="WR", proj=8.0),
        P("Bench Receiver", "BN", primary="WR", proj=12.0),
    ]


def test_override_swaps_match_via_normalization():
    swaps, skipped = sl.build_override_swaps(
        _two_player_roster(), [{"out": "D.J. Moore", "in": "bench receiver"}])
    assert swaps == [("DJ Moore", "Bench Receiver", "WR", 4.0)]
    assert skipped == []


def test_override_swaps_dedup_repeated_player():
    roster = _two_player_roster() + [P("Other WR", "WR", primary="WR", proj=5.0)]
    swaps, skipped = sl.build_override_swaps(roster, [
        {"out": "DJ Moore", "in": "Bench Receiver"},
        {"out": "Other WR", "in": "Bench Receiver"},     # reuse → must be dropped
    ])
    assert len(swaps) == 1
    assert skipped and "already used" in skipped[0]


def test_override_swaps_reject_unstartable_locked_ineligible():
    roster = [
        P("Starter WR", "WR", primary="WR", proj=8.0),
        P("Out Bench", "BN", primary="WR", proj=15.0, status="O"),
        P("Locked Bench", "BN", primary="WR", proj=15.0, locked=True),
        P("Bench QB", "BN", primary="QB", proj=25.0),
    ]
    for bad in ("Out Bench", "Locked Bench", "Bench QB"):
        swaps, skipped = sl.build_override_swaps(roster, [{"out": "Starter WR", "in": bad}])
        assert swaps == [] and len(skipped) == 1


def test_override_swaps_swap_in_must_come_from_bench():
    # a healthy player parked in an IR slot (or another starter) is a valid
    # roster match but NOT a valid swap-in — the contract is starter ↔ bench
    roster = [
        P("Starter WR", "WR", primary="WR", proj=8.0),
        P("Healthy IR Guy", "IR", primary="WR", proj=15.0),   # status "", startable
        P("Other Starter", "WR", primary="WR", proj=9.0),
    ]
    for not_bench in ("Healthy IR Guy", "Other Starter"):
        swaps, skipped = sl.build_override_swaps(
            roster, [{"out": "Starter WR", "in": not_bench}])
        assert swaps == [] and "not on bench" in skipped[0]


def test_override_swaps_unknown_names_skipped_and_reported():
    swaps, skipped = sl.build_override_swaps(
        _two_player_roster(), [{"out": "Nobody", "in": "Bench Receiver"}])
    assert swaps == []
    assert skipped == ["Nobody→Bench Receiver (no unique roster match)"]


def test_override_swaps_self_swap_rejected():
    # out and in resolving to the same player must never produce a "swap"
    swaps, skipped = sl.build_override_swaps(
        _two_player_roster(), [{"out": "DJ Moore", "in": "D.J. Moore"}])
    assert swaps == [] and "self-swap" in skipped[0]


def test_override_swaps_non_string_names_skip_not_crash():
    # LLM-generated advice can carry wrong types — must degrade, never raise
    swaps, skipped = sl.build_override_swaps(
        _two_player_roster(),
        [{"out": 42, "in": "Bench Receiver"}, {"out": ["DJ Moore"], "in": {"x": 1}}])
    assert swaps == [] and len(skipped) == 2


# ---------- decide (priority chain) ----------

def _chain_roster():
    # optimizer would swap: starter 5.0 vs bench 10.0 (gain 5.0 ≥ min_swap_gain)
    return [
        P("Weak Starter", "WR", primary="WR", proj=5.0),
        P("Strong Bench", "BN", primary="WR", proj=10.0),
        P("Other Bench", "BN", primary="WR", proj=7.0),
    ]


def test_decide_manual_beats_advice_and_optimizer(tmp_path, monkeypatch):
    (tmp_path / "lineup.json").write_text(
        json.dumps({"swaps": [{"out": "Weak Starter", "in": "Other Bench"}]}))
    _write_advice(tmp_path, _iso(1), swaps=[{"out": "Weak Starter", "in": "Strong Bench"}])
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    swaps, source, osk = sl.decide(_chain_roster())
    assert source == "manual"
    assert swaps == [("Weak Starter", "Other Bench", "WR", 2.0)]


def test_decide_manual_authoritative_even_when_nothing_validates(tmp_path, monkeypatch):
    (tmp_path / "lineup.json").write_text(
        json.dumps({"swaps": [{"out": "Typo Name", "in": "Also Wrong"}]}))
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    swaps, source, osk = sl.decide(_chain_roster())
    assert (swaps, source) == ([], "manual")
    assert len(osk) == 1     # human chose it; optimizer must not churn


def test_decide_advice_used_when_no_manual(tmp_path, monkeypatch):
    _write_advice(tmp_path, _iso(1), swaps=[{"out": "Weak Starter", "in": "Strong Bench"}])
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    swaps, source, osk = sl.decide(_chain_roster())
    assert source == "advice"
    assert swaps == [("Weak Starter", "Strong Bench", "WR", 5.0)]


def test_decide_garbage_advice_falls_back_to_optimizer(tmp_path, monkeypatch):
    # fresh advice whose entries ALL fail validation = hallucinated research
    _write_advice(tmp_path, _iso(1), swaps=[{"out": "Hallucinated", "in": "Not Real"}])
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    swaps, source, osk = sl.decide(_chain_roster())
    assert source == "optimizer"
    assert swaps == [("Weak Starter", "Strong Bench", "WR", 5.0)]


def test_decide_stale_advice_falls_back_to_optimizer(tmp_path, monkeypatch):
    _write_advice(tmp_path, _iso(sl.CFG.get("advice_max_age_hours", 6) + 1),
                  swaps=[{"out": "Weak Starter", "in": "Strong Bench"}])
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    swaps, source, osk = sl.decide(_chain_roster())
    assert source == "optimizer"


def test_decide_nothing_present_runs_optimizer(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "ROOT", tmp_path)
    swaps, source, osk = sl.decide(_chain_roster())
    assert source == "optimizer"
    assert swaps == [("Weak Starter", "Strong Bench", "WR", 5.0)]
