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

Static shared token, accepted two ways:

1. `Authorization: Bearer $CONNECTOR_TOKEN` header, or
2. `?key=$CONNECTOR_TOKEN` on the URL — **use this form for claude.ai**: the
   Add-custom-connector UI's Advanced Settings expose OAuth client id/secret,
   not a raw bearer field, so paste the full URL
   `https://<host>/mcp?key=<token>` as the connector URL.

Generate a long random token (`openssl rand -hex 32`); set it in the host's
secret store. Treat the URL itself as a secret in form 2.

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
