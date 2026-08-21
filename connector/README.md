# Yahoo Fantasy — custom Claude connector (remote MCP server)

Wraps the repo's `yahoo_api.py` as a streamable-HTTP MCP server so claude.ai
(web, iPhone, and the research Routines) can read the LIVE roster, matchup,
free agents, and league settings — and, only if explicitly enabled, apply
guarded lineup swaps.

## Tools

| Tool | Access | Notes |
|---|---|---|
| `ff_get_roster` | read | live slots, statuses, byes, projections, locks |
| `ff_get_matchup` | read | weekly head-to-head |
| `ff_get_free_agents` | read | top FAs by points, position filter |
| `ff_get_league_settings` | read | slots/scoring |
| `ff_set_lineup` | write, **disabled by default** | needs `ENABLE_WRITES=true` env **and** `confirm=true` arg; same guardrails as the executor; position swaps only — no add/drop/trade code exists in this server |

## Auth

claude.ai custom connectors support **only OAuth 2.1 or no-auth** — there is
no bearer/API-key field (anthropics/claude-ai-mcp#112, closed not-planned).
This server therefore uses "no-auth plus an unguessable URL": a shared token
that must appear in every request, accepted three ways:

1. **Path segment — use this for claude.ai:** `https://<host>/<token>/mcp`
   (the token segment is stripped server-side before the MCP app sees the path)
2. Query parameter: `https://<host>/mcp?key=<token>`
3. `Authorization: Bearer <token>` header (curl / MCP inspector testing)

Generate a long random token (`openssl rand -hex 32`); set it in the host's
secret store. **The URL is the credential — treat it like a password, and
keep `ENABLE_WRITES` unset while using URL-secret auth.** If claude.ai ever
rejects both URL forms, the fallback is implementing MCP-spec OAuth 2.1.

## Local run

```bash
pip install -r connector/requirements.txt
CONNECTOR_TOKEN=dev YAHOO_SECRETS_PATH=~/.ffl-secrets/yahoo.json \
  uvicorn server:app --port 8080     # from connector/, with repo root on PYTHONPATH
```

Smoke: `curl -s -X POST localhost:8080/mcp -H 'Authorization: Bearer dev' \
-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'`

## Deploy (gate G2)

`fly.toml` provided — see its header comments. Any host works if it gives you:
public HTTPS, env-var secrets, and a way to materialize `~/.ffl-secrets/yahoo.json`
into the container (here: base64 secret → file at release).

## License note (plan P3.T1)

This server is written against our own `yahoo_api.py`, not copied from prior
art. If code is ever adapted from `derekrbreese/fantasy-football-mcp-public`
or similar, CHECK ITS LICENSE FIRST and record the decision here.
