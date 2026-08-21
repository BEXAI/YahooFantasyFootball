#!/usr/bin/env python3
"""Plan P0.T3 — discover the team, dump the normalized roster, and RESOLVE THE
UNKNOWNS the client shipped with (projected-points field, editability field).

Run on the Mac after scripts/yahoo_auth.py. Read-only. Writes a sanitized raw
payload to tests/fixtures/roster_live.json (player names kept; guids stripped;
never any tokens — the API payload carries none, this is belt and braces).
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import yahoo_api  # noqa: E402


def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items() if k not in ("guid",)}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


def main():
    api = yahoo_api.YahooApi()
    tk = api.discover_team_key()
    print(f"team_key: {tk}   league_key: {api.league_key}")

    raw = api._request(f"/team/{tk}/roster/players")
    roster = api.read_roster()
    print(f"current_week: {api.current_week}\n")
    print(f"{'NAME':24} {'SLOT':6} {'PRI':4} {'ST':4} {'BYE':4} {'PROJ':6} LOCKED")
    for p in roster:
        print(f"{p['name'][:24]:24} {p['slot']:6} {str(p['primary'])[:4]:4} "
              f"{p['status']:4} {str(p['bye']):4} {p['proj']:<6} {p['locked']}")

    print("\n--- UNKNOWNS REPORT (paste into docs/api_notes.md) ---")
    text = json.dumps(raw)
    for probe_key in ("is_editable", "editorial", "projected", "starting_status",
                      "has_player_notes", "player_points"):
        hits = len(re.findall(re.escape(probe_key), text))
        print(f"  key ~'{probe_key}': {hits} occurrence(s)")
    print(f"  PROJ_AVAILABLE per client mapping: {yahoo_api.PROJ_AVAILABLE}")
    print("  ACTION: if projected points / editability appear under different keys,")
    print("  update yahoo_api.read_roster's TODO(P0.T3) mappings and this fixture.")

    fx = ROOT / "tests" / "fixtures" / "roster_live.json"
    fx.write_text(json.dumps(sanitize(raw), indent=1))
    print(f"\nSanitized fixture written: {fx}")


if __name__ == "__main__":
    main()
