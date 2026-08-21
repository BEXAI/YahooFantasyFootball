# API/Connector upgrade — execution status

Tracks `docs/API_CONNECTOR_UPGRADE_PLAN.json` task by task. Update this file in
the same commit as the work it records.

| Task | Status | Evidence |
|---|---|---|
| P0.T1 create Yahoo dev app | **WAITING ON HUMAN** | developer.yahoo.com → Fantasy Sports Read/Write |
| P0.T2 scripts/yahoo_auth.py | DONE (code) | committed; exercised only at P0.T1 handoff |
| P0.T3 scripts/yahoo_probe.py | DONE (code) | committed; resolves TODO(P0.T3) fields on first live run |
| P0.T4 scripts/yahoo_write_proof.py | DONE (code) — **gate G0 awaits human run** | supervised swap+revert with lock-window guard |
| P1.T1 yahoo_api.py reads + tokens | DONE | 79-test suite incl. token refresh/rotation, roster mapping, coverage-week, dedupe |
| P1.T2 set_positions writer | DONE | XML shape + escaping + error-taxonomy tests (locked/400/401/refresh) |
| P1.T3 name→player_key bridge | DONE | changes_for_swap tests (normalized + unknown-name) |
| N1 no-transactions guard | DONE | tests grep yahoo_api.py + connector/server.py |
| N5 no-committed-secrets guard | DONE | test scans all tracked files |
| P2.T1 write_path config switch | DONE | default browser; test pins it |
| P2.T2 api_main READ/WRITE/VERIFY | DONE | same finish() contract; LOGIN_REQUIRED exit-2 test |
| P2.T3 run.sh + README docs | DONE | §1.5a Write paths |
| P2.T4 supervised live api run | **WAITING ON HUMAN** (after G0) | |
| P2.T5 two clean scheduled windows | **WAITING** (gate G1) | |
| P3.T1 license check | OPEN (human) | clean-room server shipped meanwhile — note in connector/README.md |
| P3.T2 server skeleton | DONE | MCP SDK 2.0, streamable HTTP, bearer auth; import+tool-registration verified |
| P3.T3 read tools | DONE (code) | live check happens at deploy smoke |
| P3.T4 ff_set_lineup gated write | DONE | ENABLE_WRITES env + confirm arg + executor-grade validation |
| P3.T5 Dockerfile + fly.toml | DONE | build ctx = repo root; secrets via host store |
| P3.T6 deploy + smoke | **WAITING ON HUMAN** (gate G2: pick host) | |
| P4.T1 add connector at claude.ai | **WAITING ON HUMAN** | Settings → Connectors → + |
| P4.T2 recreate Routines w/ connector | WAITING (after P4.T1) | |
| P4.T3 ROUTINE_PROMPT v2 | WAITING (after P4.T1) | current prompt stays live until connector exists |
| P4.T4 smoke-fire with connector | WAITING | |
| P5.* transition/decommission | GATED (G1/G3) | |

**Next human actions, in order:** P0.T1 (create the app) → run `scripts/yahoo_auth.py`,
`scripts/yahoo_probe.py`, `scripts/yahoo_write_proof.py` on the Mac (gate G0) →
report results back; then G2 host choice; then P4.T1 connector add.
