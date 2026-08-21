#!/usr/bin/env python3
"""Custom Claude connector — remote MCP server wrapping yahoo_api.py (plan P3).

Transport: streamable HTTP (what claude.ai custom connectors speak).
Auth: static bearer token — every request must carry
      Authorization: Bearer $CONNECTOR_TOKEN  (set both in the host's secret
      store and in the connector's Advanced Settings on claude.ai).
Secrets: Yahoo tokens come from $YAHOO_SECRETS_PATH (default ~/.ffl-secrets/
      yahoo.json) — injected by the host, never baked into the image (plan N5).
Writes: ff_set_lineup ships DISABLED. It runs only when ENABLE_WRITES=true in
      the environment AND the caller passes confirm=true, and it enforces the
      same guardrails as the executor (plan N8). There is deliberately no
      add/drop/trade capability anywhere in this server (plan N1).

Run locally:   CONNECTOR_TOKEN=dev uvicorn server:app --port 8080
Deploy:        see connector/README.md (Dockerfile + fly.toml provided).
"""
import os
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mcp.server import MCPServer  # noqa: E402  (MCP SDK >= 2.0)

import yahoo_api  # noqa: E402
from ffl_common import norm_name, roster_index  # noqa: E402

mcp = MCPServer("Yahoo Fantasy")

_api = None


def api():
    global _api
    if _api is None:
        _api = yahoo_api.YahooApi()
    return _api


@mcp.tool()
def ff_get_roster(week: int | None = None) -> dict:
    """Current roster with LIVE slot assignments. Each player: name, slot
    (QB/RB/WR/TE/W/R/T/K/DEF = starting, BN = bench, IR), primary position,
    injury status ('' healthy, Q/D startable-risky, O/IR/SUSP/NA/PUP/NFI not
    startable), bye (True = on bye this week), proj (Yahoo projected points;
    0.0 everywhere means projections unavailable), locked (game started)."""
    roster = api().read_roster(week)
    return {"week": api().current_week,
            "proj_available": yahoo_api.PROJ_AVAILABLE,
            "players": [{k: v for k, v in p.items() if k != "player_key"}
                        for p in roster]}


@mcp.tool()
def ff_get_matchup(week: int | None = None) -> dict:
    """This week's head-to-head matchup (opponent, points, projections) as
    returned by Yahoo. Raw-ish payload — summarize before showing a human."""
    return api().read_matchup(week)


@mcp.tool()
def ff_get_free_agents(position: str | None = None, count: int = 25) -> dict:
    """Top free agents in the league, optionally filtered by position
    (QB/RB/WR/TE/K/DEF), sorted by points. READ ONLY — this connector cannot
    add, drop, or trade players, ever."""
    return api().read_free_agents(position, count)


@mcp.tool()
def ff_get_league_settings() -> dict:
    """League settings: roster slots, scoring categories, playoff structure."""
    return api().read_league_settings()


@mcp.tool()
def ff_set_lineup(swaps: list[dict], confirm: bool = False) -> dict:
    """Apply lineup position swaps. swaps = [{"out": <current starter name>,
    "in": <bench player name>}]. Requires confirm=true AND server-side
    ENABLE_WRITES=true; refuses otherwise. Enforces: unique name match,
    swap-in currently on bench, swap-in startable (no BYE/O/IR/SUSP/NA/PUP/
    NFI), neither player locked. Position changes only — never add/drop."""
    if os.environ.get("ENABLE_WRITES", "").lower() != "true":
        return {"applied": [], "skipped": [],
                "refused": "writes are disabled on this server (ENABLE_WRITES != true)"}
    if not confirm:
        return {"applied": [], "skipped": [],
                "refused": "pass confirm=true after the human has approved these exact swaps"}
    roster = api().read_roster()
    week = api().current_week
    idx = roster_index(roster)
    applied, skipped = [], []
    used = set()
    for s in swaps if isinstance(swaps, list) else []:
        out_raw = s.get("out") if isinstance(s, dict) else None
        in_raw = s.get("in") if isinstance(s, dict) else None
        label = f"{out_raw}→{in_raw}"
        st, bn = idx.get(norm_name(out_raw)), idx.get(norm_name(in_raw))
        if not st or not bn or st is bn:
            skipped.append(f"{label} (no unique roster match)")
        elif st["name"] in used or bn["name"] in used:
            skipped.append(f"{label} (player already used)")
        elif bn["slot"] != "BN":
            skipped.append(f"{label} (swap-in not on bench)")
        elif bn["bye"] or bn["status"] in yahoo_api.BAD_STATUSES:
            skipped.append(f"{label} (not startable)")
        elif bn["locked"] or st["locked"]:
            skipped.append(f"{label} (locked)")
        elif st["slot"] in ("BN", "IR"):
            skipped.append(f"{label} (swap-out not a starter)")
        else:
            try:
                api().set_positions([(bn["player_key"], st["slot"]),
                                     (st["player_key"], "BN")], week)
                applied.append(label)
                used.update((st["name"], bn["name"]))
            except yahoo_api.LockedError:
                skipped.append(f"{label} (locked)")
            except yahoo_api.YahooApiError as e:
                skipped.append(f"{label} (api-error: {type(e).__name__})")
    # verify-then-report (plan N7)
    fresh = {p["name"]: p["slot"] for p in api().read_roster(week)}
    return {"applied": applied, "skipped": skipped,
            "verified_slots": fresh, "week": week}


class BearerAuth:
    """Minimal ASGI middleware. Accepts the shared token either as
    `Authorization: Bearer <token>` OR as a `?key=<token>` query parameter —
    the query form exists because the claude.ai Add-custom-connector UI has
    been reported to expose only OAuth client id/secret under Advanced
    Settings, not a raw bearer field (anthropics/claude-ai-mcp#112); with the
    query form the whole URL `https://host/mcp?key=<token>` can be pasted as
    the connector URL. Non-http scopes (lifespan) pass through untouched or
    the server can't even start."""

    def __init__(self, inner, token):
        self.inner, self.token = inner, token

    def _authorized(self, scope):
        headers = {k.decode().lower(): v.decode()
                   for k, v in scope.get("headers", [])}
        if headers.get("authorization") == f"Bearer {self.token}":
            return True
        query = urllib.parse.parse_qs(scope.get("query_string", b"").decode())
        return self.token in query.get("key", [])

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not self._authorized(scope):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await self.inner(scope, receive, send)


_token = os.environ.get("CONNECTOR_TOKEN")
if not _token:
    raise SystemExit("CONNECTOR_TOKEN env var is required — refusing to start open.")
app = BearerAuth(mcp.streamable_http_app(stateless_http=True), _token)
