#!/usr/bin/env python3
"""Plan P0.T4 / gate G0 — SUPERVISED benign write + revert on the real league.

Proves fspt-w works end to end: swap one startable bench player with one
same-position starter, verify via re-read, revert, verify again. Refuses to
run near typical lock windows unless --force. HUMAN AT THE KEYBOARD REQUIRED —
it will not proceed without typed confirmation at each step.
"""
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import yahoo_api  # noqa: E402
from ffl_common import norm_name  # noqa: E402


def near_lock_window(now=None):
    now = now or datetime.datetime.now()
    wd, hr = now.weekday(), now.hour     # Mon=0 ... Sun=6, local (ET on the Mac)
    return (wd == 3 and hr >= 17) or (wd == 6 and hr >= 10) or (wd == 0 and hr >= 17)


def pick_pair(roster):
    """First (starter, bench) pair sharing a primary position, both startable
    and unlocked — the most benign possible swap."""
    for st in roster:
        if st["slot"] in ("BN", "IR") or st["locked"] or st["status"] in yahoo_api.BAD_STATUSES:
            continue
        for bn in roster:
            if (bn["slot"] == "BN" and not bn["locked"] and not bn["bye"]
                    and bn["status"] not in yahoo_api.BAD_STATUSES
                    and bn["primary"] == st["primary"]):
                return st, bn
    return None, None


def confirm(word):
    if input(f"Type {word} to proceed (anything else aborts): ").strip() != word:
        print("Aborted — no changes made beyond those already confirmed.")
        sys.exit(1)


def slot_of(roster, name):
    return next((p["slot"] for p in roster if norm_name(p["name"]) == norm_name(name)), None)


def main():
    if near_lock_window() and "--force" not in sys.argv:
        sys.exit("Refusing near a lock window (Thu 17:00+/Sun 10:00+/Mon 17:00+ local). "
                 "Re-run earlier in the day, or --force if you accept the risk.")
    api = yahoo_api.YahooApi()
    roster = api.read_roster()
    week = api.current_week
    st, bn = pick_pair(roster)
    if not st:
        sys.exit("No benign same-position starter/bench pair available — try another day.")
    print(f"Week {week} proof pair: OUT {st['name']} ({st['slot']}) ↔ IN {bn['name']} (BN)")

    confirm("SWAP")
    api.set_positions([(bn["player_key"], st["slot"]), (st["player_key"], "BN")], week)
    fresh = api.read_roster(week)
    ok = slot_of(fresh, bn["name"]) == st["slot"] and slot_of(fresh, st["name"]) == "BN"
    print(f"swap applied+verified: {ok}")
    if not ok:
        sys.exit("VERIFY FAILED after swap — inspect the Yahoo app, revert manually if needed.")

    confirm("REVERT")
    api.set_positions([(st["player_key"], st["slot"]), (bn["player_key"], "BN")], week)
    fresh = api.read_roster(week)
    ok = slot_of(fresh, st["name"]) == st["slot"] and slot_of(fresh, bn["name"]) == "BN"
    print(f"revert applied+verified: {ok}")
    if not ok:
        sys.exit("VERIFY FAILED after revert — fix in the Yahoo app before relying on this.")

    print("\nWRITE PROOF PASSED (gate G0). Record the run in docs/api_notes.md:")
    print("  - the exact XML accepted (yahoo_api.build_roster_xml shape)")
    print("  - any locked-player error strings you saw, appended to _LOCKED_MARKERS")


if __name__ == "__main__":
    main()
