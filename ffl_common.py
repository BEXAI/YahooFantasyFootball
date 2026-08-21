"""Shared, dependency-free helpers used by both the executor (set_lineup.py)
and the Yahoo API client (yahoo_api.py) / MCP connector.

Kept free of Playwright, config, and network imports on purpose: the connector
container installs neither Playwright nor the executor's runtime state.
"""
import re

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(s):
    """Match names across renderings: 'D.J. Moore' == 'DJ Moore', drop Jr/III etc.
    Total over any input: a non-string (LLM-generated advice can carry wrong
    types) normalizes to "" and simply never matches — it must not crash."""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"[^a-z0-9 ]", "", s.casefold())
    return " ".join(p for p in s.split() if p not in _SUFFIXES)


def roster_index(roster):
    """normalized name -> item; a normalized-name collision maps to None so an
    ambiguous lookup is skipped, never guessed. Works for any list of dicts
    with a 'name' key (executor player dicts or API player dicts)."""
    idx = {}
    for p in roster:
        k = norm_name(p["name"])
        idx[k] = None if k in idx else p
    return idx
