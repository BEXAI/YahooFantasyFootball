# PLAN.md — Cloud-Native Migration ("no MacBook, no Chrome")

**Plan ID:** `ffl-cloud-native-v2`
**Created:** 2026-08-20
**Repository:** https://github.com/BEXAI/YahooFantasyFootball (branch `main`)
**Supersedes (partially):** `docs/API_CONNECTOR_UPGRADE_PLAN.json` — see [§13 Relationship to the v1 plan](#13-relationship-to-the-v1-plan). That file stays authoritative for the Yahoo-API and connector *internals*; this file replaces its execution model.

**Audience:** an AI LLM coding agent executing this plan task-by-task, with the repo owner ("Nathaniel") available only at marked decision gates.

**Revision v2 (2026-08-21):** corrected after an adversarial multi-agent review of v1 (66 findings across six lenses; independently verified before application). Six defects were **critical**: the executor entrypoint was never created by any task; scheduled runs would have executed **live** rather than dry; shallow checkout silently broke override freshness; a committed manual override could permanently starve the optimizer; the cron never covered the Monday-night lock or any Saturday; and the `player_key`/`player_id` warning in v1 was **false** and has been retracted. Claims verified by executing the real `decide()` are marked as such.

---

## 1. Objective

Move the FFL Agent off the MacBook and off Chrome entirely. After this plan:

- The lineup is set by a **GitHub Actions scheduled workflow** calling the Yahoo Fantasy Sports API over OAuth2 `fspt-w`. No browser, no cookies, no DOM, no `pmset`, no `launchd`, no machine that has to be awake.
- **Claude (Routines / Cowork) stays advisory** — it researches the slate and commits `advice/lineup.json`. It does not hold the write path. See [§3](#3-architectural-decision-why-claude-does-not-execute) for why this is a safety property, not a limitation.
- A **remote MCP connector** gives Claude sessions and the owner's phone live roster/matchup data — Claude's *eyes*, not its hands.
- The MacBook becomes an optional manual runner. Turning it off costs nothing.

**Definition of done:** two consecutive live game windows executed by GitHub Actions with the Mac powered off, `write_path=api`, suite green, browser path deleted, README describing the shipped architecture.

---

## 2. What already exists (verified by reading the code — do not re-derive)

The migration is **mostly plumbing, not a rewrite**. `set_lineup.py:399 api_main()` is already a complete browser-free READ → DECIDE → WRITE → VERIFY → REPORT cycle, and `yahoo_api.py` is a finished, tested OAuth2 client. 82 tests pass on a clean clone.

| Asset | State | Verdict |
|---|---|---|
| `yahoo_api.py` (364 ln) | TokenStore w/ flock + auto-refresh, `read_roster`, `read_matchup`, `read_free_agents`, `read_league_settings`, `set_positions`, `changes_for_swap`. Stdlib only. | **Keep as-is.** Two `TODO(P0.T3)` field pins remain (`yahoo_api.py:305`, `:308`). |
| `set_lineup.py:399 api_main()` | Full API cycle incl. per-swap PUT, verify re-read, `finish()` contract | **Keep.** Needs config/state/secrets plumbing only. |
| `decide()` / `plan_swaps()` / `build_override_swaps()` / `startable()` / `eligible()` (`set_lineup.py:133–279`) | The N2 guardrail logic | **Freeze. Byte-for-byte where possible.** This is the crown jewel; the 82 tests exist to protect it. |
| `finish()` (`set_lineup.py:59`) | status/exit-code/ntfy contract | **Keep contract.** Make output path configurable. |
| `ffl_common.py` | `norm_name`, `roster_index` | Keep. |
| `advice/` data channel + `generated_at` freshness (`set_lineup.py:170`) | Routine → git → executor | Keep. Extraction moves from `run.sh` to the workflow. |
| `connector/` (206 ln) | FastMCP server, read tools + gated write tool | Keep, but **deferred behind the executor** — see [§9](#9-phase-5--connector--claude-layer). |
| `tests/` (82) | Browser-free decision-logic + API tests | Keep; extend. Floor: never fewer green than today. |

### 2.1 Four blockers found in the current code

These are the actual work. Each is a specific, located defect for the cloud target:

1. **`set_lineup.py:23` imports Playwright at module top level.** Even `write_path=api` requires Chromium installed (273 MB, ~40 s on a runner). Tests import `set_lineup`, so they inherit the dependency too. → Task **1.1**.
2. **`set_lineup.py:28 load_config()` hard-requires `config.json`**, which is gitignored (`.gitignore:7`) and therefore *does not exist on a runner*. `set_lineup.py:38` also builds `TEAM_URL` from `league_id`/`team_id` at import time — values the API path never uses. → Task **1.2**.
3. **`.gitignore:5` ignores `PAUSED` unanchored.** The pattern has no leading slash, so it matches at *any depth* — `control/PAUSED` would be silently invisible too. The phone-driven kill switch cannot work until this is fixed. → Task **1.5**.
4. **`load_manual_override()` (`set_lineup.py:158`) uses file mtime.** On a runner every file is checked out fresh, so mtime is the checkout time — every override would look permanently fresh. This is the *exact* bug `load_advice()` already documents and avoids at `set_lineup.py:172`. → Task **1.5**.

### 2.2 Coverage gap the migration should close

The Mac design fires only **Thu 17:30** and **Sun 10:30 ET**. It has no coverage for Sunday late games (16:05/16:25), SNF (20:20), MNF, or Saturday games. A player ruled out at 15:00 for a 16:25 lock is never replaced. Free runners remove the reason for that limitation — see [§8 Phase 4](#8-phase-4--expand-coverage).

---

## 3. Architectural decision: why Claude does not execute

The owner asked for execution via "scheduled Claude Routines or Cowork and GitHub Actions." Those compose, but **not symmetrically** — and getting this wrong is the highest-consequence mistake available in this plan. The split is:

> **GitHub Actions executes. Claude advises. The connector is Claude's eyes.**

Evidence for keeping the LLM out of the write path — the first two are this repo's own verified findings, not speculation:

1. **Cloud egress is restricted.** `docs/API_CONNECTOR_UPGRADE_PLAN.json` → `verified_environment_constraints`: *"the Claude cloud environment's egress proxy blocks arbitrary hosts (ntfy.sh confirmed blocked)."* An executor that cannot alert on failure is not an executor.
2. **Fresh trigger-fired sessions cannot complete git pushes** (held for review). The Routines already had to be re-pointed at a persistent session because of this.
3. **No firing SLA.** A Routine that fires late, fires twice, or reasons its way into an unplanned action at 12:58 for a 13:00 lock has no bounded failure mode.
4. **The existing plan already warns about this.** Gate G3 in the v1 plan: enabling connector writes *"exposes the tool to EVERY session holding the connector — including the research Routines — bypassing the Mac's PAUSED file, the veto window, and executor precedence."*
5. GitHub Actions is deterministic, free, already runs this repo's CI, holds secrets natively, and has unrestricted egress (ntfy + healthchecks both reachable).

Claude keeps the job it is uniquely good at — reading injury reports, weather, beat-writer noise, and Vegas lines at 20:00 UTC Thursday, then committing a recommendation into a file. A deterministic Python program decides whether to trust it. That is the existing fallback chain (`set_lineup.py:235 decide()`), and it survives unchanged.

A gated tier-2 escape hatch exists in [§10 Phase 6](#10-phase-6--gated-optional-tracks) for the case where Actions itself proves unreliable. It is **off by default and must stay off** unless Gate G5 fails.

---

## 4. Target architecture

```
GitHub repo (PRIVATE — Gate G3)
│
├── .github/workflows/lineup.yml        ◀── THE EXECUTOR
│     cron (UTC, blanket) + workflow_dispatch(dry_run, force, mode)
│     concurrency: ffl-executor (never two at once)
│     python ffl_execute.py
│       ├─ 0  window gate (zoneinfo America/New_York) — DST-proof, exits <1s if outside
│       ├─ 0b PAUSED gate (control/PAUSED committed, or repo var FFL_PAUSED)
│       ├─ 1  READ    yahoo_api.read_roster()          — OAuth2, no browser
│       ├─ 2  DECIDE  decide()  [UNCHANGED CODE]
│       │        control/lineup.json (git-commit-time fresh) > advice > optimizer
│       ├─ 3  WRITE   one set_positions PUT per swap
│       ├─ 4  VERIFY  re-read roster, confirm each swap        (N7)
│       └─ 5  REPORT  ntfy push · healthchecks ping · artifact upload
│                     · optional state-branch commit
│
├── advice/lineup.json      ◀── Claude Routines / Cowork commit here (advisory only)
├── control/PAUSED          ◀── phone kill switch: commit to stop, delete to resume
├── control/lineup.json     ◀── phone manual override: beats advice + optimizer
│
└── connector/              ◀── remote MCP on Render/Fly → Claude's live eyes (read-only)
```

**Secrets** live in GitHub Actions encrypted secrets, never the repo (N5 unchanged).
**No machine of the owner's is in this diagram.** That is the point.

---

## 5. Non-negotiables

`N1`–`N8` carry over verbatim from `docs/API_CONNECTOR_UPGRADE_PLAN.json` and remain enforced by tests. Restated in brief, then extended:

- **N1** Position swaps only. No `/transactions` code path, ever. Tests grep `yahoo_api.py` and `connector/server.py`.
- **N2** Never start BYE/O/IR/SUSP/NA/PUP/NFI; never move a locked player; `min_swap_gain` threshold; each player in at most one swap. **`decide()` is the single source of lineup logic** — write paths consume its output and never reimplement it.
- **N3** `DRY_RUN=1` and PAUSED suppress **all** writes on every path.
- **N4** Fail-safe chain preserved: connector down → Routine falls back to web research; advice absent/stale/garbage → optimizer; write failure → `PARTIAL` via the same `finish()`.
- **N5** Secrets never enter the repo, history, fixtures, logs, or `last_status.json`.
- **N6** Suite green after every task. New behavior ships with new tests.
- **N7** Verify-then-report: after any write, re-read from the API and confirm before counting it done. `finish()` **always** runs.
- **N8** Connector write tool ships disabled, requires `confirm=true`, applies N2 server-side.

New for the cloud target:

- **N9 — Claude never holds the primary write path.** Routines and Cowork sessions produce `advice/*` commits and nothing else. Determinism at lock time is a safety property. Violating this requires Gate G5 to have failed *and* explicit owner sign-off.
- **N10 — Nothing secret reaches a log, artifact, committed state file, or connector response.** Two separate mechanisms are required, because they cover different surfaces:
  - **Logs.** GitHub masks *registered* secrets only; values **derived** at runtime (a fetched access token, a rotated refresh token) are **not** masked automatically and must be passed to `::add-mask::` the moment they exist.
  - **Files.** `::add-mask::` is a log-rendering feature with **no effect on files**. An artifact or committed state file containing a token uploads verbatim. Anything written to `FFL_STATE_DIR` must therefore be redacted *at write time*, and the upload step must run a token-shaped-substring scan that **fails the job** rather than uploading.

  A test must assert no token-shaped substring appears in `last_status.json`, the roster evidence files, or stdout.
- **N11 — The repo is private before any Yahoo credential is added to Actions secrets.** Gate G3.
- **N12 — Every scheduled run must be idempotent.** Running the cycle N times inside one window must converge, not churn.

  **This is a property to be BUILT, not an assumption — it is false for the current code.** Two counterexamples, both verified by executing the real `decide()`:
  - **Locked unstartable starter loops forever.** Pass 1 (`set_lineup.py:259–267`) does not check `st["locked"]` — only pass 2 does (`set_lineup.py:270`). An OUT starter whose game has kicked off yields the identical impossible swap on every run: `plan_swaps()` returned `[('Injured RB','Bench RB','RB',-3.0)]` on runs 1, 2 and 3. Every 20 minutes that is a `PARTIAL` and an ntfy push, indefinitely.
  - **A committed manual override starves the optimizer permanently.** See Task 1.5.

  Task 1.7 must fix both before any blanket cron goes live. Do **not** treat N12 as satisfied by the existing code.
- **N13 — The executor adds no runtime dependency beyond the Python standard library.** `yahoo_api.py` is already stdlib-only; keep it that way. Runner install time ≈ 0 and the supply-chain surface stays nil. (`pytest`/`pyflakes` are dev-only.)
- **N14 — No unbounded write authority.** Split, because the two halves have very different costs:
  - **`max_swaps_per_run` (default 4)** — pure, needs no persistence: a bound on the apply loop in `api_main()` (`set_lineup.py:436`). **Implement in Task 1.7, before any live write.**
  - **`max_swaps_per_day` (default 8)** — requires state outliving an ephemeral run. There is no store for this today. Either implement it against the state branch in Task 1.4, or **drop the daily cap explicitly** and say so in `docs/api_notes.md`. Do not leave it declared-but-unenforced: a non-negotiable that no task implements is worse than an absent one, because reviewers will assume it holds.

  Exceeding an enforced cap reports `PARTIAL` and alerts rather than proceeding.

---

## 6. Phase 0 — Prove the premises

**Goal:** kill the design cheaply if it cannot work. Nothing in Phases 1–6 is worth building until Gates G0–G2 pass, and **G3 (repo private) must clear before Task 0.6** puts the first credential into Actions. Every task here is fast.

> **Timing rule for all live tasks:** never run a write test within 3 hours of any lock.
>
> **`scripts/yahoo_write_proof.py` does NOT actually enforce this.** `near_lock_window()` (`scripts/yahoo_write_proof.py:19`) is a weekday/hour heuristic in the machine's **local** time covering only Thu ≥17:00, Sun ≥09:00 and Mon ≥17:00 — no Saturday coverage, no real lock arithmetic — and `--force` bypasses it entirely (`:51`). Treat the 3-hour rule as a **human discipline**, not a guardrail, or harden the function first. Do not weaken what is there.

### 0.1 — Create the Yahoo developer app · *human*

At https://developer.yahoo.com/apps/create/ :
- API Permissions → **Fantasy Sports** → **Read/Write** (not Read)
- Redirect URI: `https://localhost:8080` (Yahoo requires https; the code is copied by hand from the redirected URL)
- Record Client ID + Client Secret into `~/.ffl-secrets/yahoo.json`, `chmod 600`

**Research note (verified 2026-08):** no evidence anywhere of Yahoo removing or denying the write scope. The common failure is requesting `fspt-w` at token time while the *app* was registered Read-only — the two must match. If only Read is grantable, record that and go to Gate G0.

**Accept:** app exists with Fantasy Sports Read/Write listed.

### 0.2 — OAuth bootstrap · *human, one-time, any browser*

```bash
.venv/bin/python scripts/yahoo_auth.py
```
Any machine with a browser works; this does **not** create a Mac dependency. Produces `~/.ffl-secrets/yahoo.json` at 0600.

**Accept:** secrets file written; a follow-up `grant_type=refresh_token` call returns a new access token.

### 0.3 — Live probe: pin the two unknown fields · *code + human*

```bash
.venv/bin/python scripts/yahoo_probe.py
```

Resolve both `TODO(P0.T3)` markers against the real payload:
- `yahoo_api.py:300–305` — **projected points**. Currently guesses `projected_points`. If found, set `PROJ_AVAILABLE=True`; if genuinely absent, `proj` stays `0.0` and the optimizer safely degrades to pass 1 (still fixes BYE/OUT).
- `yahoo_api.py:306–308` — **per-player editability/locked**. Currently guesses `is_editable`. Yahoo may expose editability only at roster level; if so, keep `locked=False` and rely on per-PUT `LockedError` + the N7 verify re-read.

Commit the sanitized fixture the probe actually writes — `tests/fixtures/roster_live.json` (`scripts/yahoo_probe.py:53`), creating `tests/fixtures/` which does not yet exist. Names may stay; `_STRIP_KEYS` (`:19`) already removes guid/email/image_url/managers. Record findings in `docs/api_notes.md`.

**Accept:** probe prints the real roster with slot assignments; both fields either pinned or documented as absent-with-fallback; fixture committed; tests green.

### 0.4 — Refresh-token rotation experiment · *code* · **DECISIVE**

**This task determines whether Phase 2 needs credential write-back machinery at all.** Do it before designing Phase 2's token handling.

Write `scripts/yahoo_token_probe.py`: perform two consecutive `grant_type=refresh_token` calls and compare the returned `refresh_token` values. Print `ROTATES` or `STABLE`. Print **no token material** — compare hashes, report the verdict only (N10).

- **ROTATES** → durable storage required; choose at Gate G2.
- **STABLE** → *observed* stable. This is **not** a licence to skip write-back. Yahoo documents rotation as discretionary: it may issue a new refresh token on any refresh and revokes the old one when it does. Two probe calls establish only that it did not rotate *this time*.

**Therefore the write-back path in Task 2.2 is MANDATORY regardless of the verdict.** A `STABLE` result only downgrades its urgency and lets you defer the choice of store. An executor that assumes stability will run fine for weeks and then lock out silently mid-season, at a lock window, with no recovery path but a human at a browser.

`yahoo_api.py:159–160` already persists a rotated token locally, so only the *stateless-runner* case is new.

**Accept:** verdict printed and recorded in `docs/api_notes.md`. → **Gate G2**

### 0.5 — Supervised write proof · *human at keyboard*

```bash
.venv/bin/python scripts/yahoo_write_proof.py
```

Benign same-position swap, then revert. Both PUTs 2xx, both re-GETs reflect the state.

> **Identifier shape — resolved, no action needed.** An earlier draft of this plan claimed `yahoo_api.build_roster_xml()` might be sending the wrong identifier because the `yahoo_fantasy_api` library's API takes `player_id`. **That was wrong.** The library takes `player_id` in its *Python signature* and then builds `<player_key>` in the XML by prefixing the league id — `yahoo_fantasy_api/team.py:492` literally calls `doc.createElement('player_key')`. Yahoo's roster PUT accepts `<player_key>`, which is exactly what `yahoo_api.py:336` already sends. Do not "fix" this.

Record the exact accepted XML in `docs/api_notes.md` anyway — it is the reference for every later write.

Also capture any locked-player error strings and extend `_LOCKED_MARKERS` (`yahoo_api.py:59`) with the real ones.

**Accept:** write + revert both confirmed; XML shape and lock strings recorded. → **Gate G0**

### 0.6 — Datacenter-IP proof · *code* · **HIGHEST UNKNOWN**

**Blocked on Gate G3.** This task puts a live Yahoo credential into Actions secrets, and N11 forbids that on a public repo. Do not create `probe.yml` or add any secret until the owner has confirmed the repo is private.

Everything else assumes Yahoo will serve an authenticated request from a GitHub-hosted runner (Azure IP space). The Mac design leaned on a *residential* IP on purpose.

**Calibrate the failure mode correctly.** Yahoo Fantasy's HTTP 999 is reported to be a request-volume block scoped to the **registered application credentials**, not to the caller's IP. If that is right, moving the runner would *not* fix a real 999 — the remediation is reducing call volume per app (fewer firings, caching the roster read within a window) or registering a second app. Treat "is it IP-scoped or app-scoped?" as the actual question this task answers, and record the evidence either way.

Add `.github/workflows/probe.yml` — `workflow_dispatch` only, **read-only**, no writes under any circumstance. Note it must exist on the **default branch** to be dispatchable at all (see §14):
- run `read_roster()` from an `ubuntu-latest` runner using a temporary secret
- print status codes and whether any 999/429 occurred
- run it ~10 times across a day to catch rate-based blocking, not just a cold single call

`yahoo_api.py:242` already retries 429/999 with 2/4/8 s backoff — check whether the retries are *being consumed*, not merely whether the call eventually succeeded. Consumed retries on every call is a yellow flag worth recording.

**Accept:** ≥10 consecutive successful authenticated reads from GitHub runners with no 999. → **Gate G1**

> **If G1 fails:** stop and first establish *why*. If 999s are app-scoped, a different host will not help — cut firing frequency and cache the roster read per window. Only if the block is genuinely IP-scoped does relocating help: a self-hosted runner (a Raspberry Pi on the home network — README §3.3 already pre-plans this migration) or Render/Fly cron. Never work around a block with retry loops or IP rotation.

---

## 7. Phase 1 — Decouple the code from Mac and browser

**Goal:** `set_lineup.py`'s API path runs on a bare Python 3.12 container with no config file, no browser, and no home directory. Pure refactor — **no behavior change**, suite green at every commit.

### 1.1 — Split the browser path out

Move `browser_main()`, `parse_roster()`, `guard_login()`, `player_row()`, `click_slot_control()`, `enter_swap_mode()`, `execute()` and the `ROW_SEL` DOM contract into a new `browser_path.py`. Delete the top-level import at `set_lineup.py:23`; `main()` imports `browser_path` **lazily**, inside the `write_path == "browser"` branch only.

Also update `.github/workflows/tests.yml:17` in this task: change `pip install -r requirements.txt pytest` to `pip install pytest pyflakes`, so CI proves the browser-free import path rather than masking it by installing Playwright.

**Accept:** new test asserts `"playwright" not in sys.modules` after importing `set_lineup` and running the api path; `pip uninstall playwright` leaves the suite green; existing 82 tests still pass; CI green with no Playwright installed.

### 1.2 — Config resolution: file → env → defaults

Add `ffl_config.py` with precedence **env var > `config.json` > `config.defaults.json`**:

- Commit `config.defaults.json` with the **non-secret** knobs: `min_swap_gain`, `lineup_json_max_age_hours`, `advice_max_age_hours`, `slot_eligibility`, `bad_statuses`, `write_path`, `max_swaps_per_run`, `max_swaps_per_day`.
- Env overlay `FFL_*` (e.g. `FFL_NTFY_TOPIC`, `FFL_HEALTHCHECKS_URL`, `FFL_MIN_SWAP_GAIN`).
- `league_id`/`team_id` become **optional**: `TEAM_URL` (`set_lineup.py:38`) is browser-only. Make it lazy so a missing id cannot crash the API path at import time.
- `load_config()` must no longer `sys.exit` when `config.json` is absent — that is now the *normal* case.

Keep `tests/conftest.py`'s pinning behavior working (it copies `config.example.json` → `config.json`; it may need to target `config.defaults.json` instead).

**Accept:** `python -c "import set_lineup"` succeeds in a directory with no `config.json`; new precedence tests; suite green.

### 1.3 — Secrets from the environment

`TokenStore` (`yahoo_api.py:63`) is file-based with `fcntl` locking. Add an env path: `YAHOO_TOKENS_JSON` (whole secrets blob) is materialized to a `0600` temp file at startup, and `TokenStore` uses it. `fcntl` works on Linux runners, so the locking code is unchanged.

Emit `::add-mask::` for `access_token` and `refresh_token` **the moment they are read or refreshed** (N10). Never print the blob.

**The exact secret inventory** — no other task defines these, so define them here. Repository *secrets*: `YAHOO_TOKENS_JSON` (the whole `~/.ffl-secrets/yahoo.json` blob), `FFL_HEALTHCHECKS_URL`, `GH_SECRETS_PAT` (only if Gate G2 chooses GitHub write-back). Repository *variables* (non-secret, editable from the GitHub UI): `FFL_NTFY_TOPIC`, `FFL_PAUSED`, `FFL_LIVE`. The workflow maps each into `env:` at the job level; nothing is read from `config.json` on a runner.

**Accept:** api path runs with only `YAHOO_TOKENS_JSON` set and no `~/.ffl-secrets/`; test asserts no token substring in `last_status.json` or captured stdout.

### 1.4 — State and evidence without a filesystem

`finish()` (`set_lineup.py:59`) writes `logs/last_status.json`. Make the directory configurable (`FFL_STATE_DIR`, default `logs/`). The workflow uploads it plus `api_roster_before/after_*.json` as artifacts.

**Also build the undo path.** The plan otherwise has no way to reverse a bad automated write — and once Phase 3 deletes `run.sh`/`teardown.sh`, no local tooling either. `api_roster_before_*.json` already captures the exact pre-write lineup; add `ffl_execute.py --restore <file>` that reads one of those snapshots and PUTs every player back to its recorded `slot`. It is the same `set_positions` path, so it inherits N7 verification and the locked-player semantics for free.

**Accept:** `FFL_STATE_DIR=/tmp/x` places every artifact there; `finish()` still always runs and still exits with the same codes; `--restore` round-trips a swap on a fixture and refuses to run without `--confirm`.

### 1.5 — Control surfaces that work from a phone

Three fixes, all consequences of "there is no longer a machine to SSH into":

1. **`.gitignore:5`** — change `PAUSED` to `/PAUSED`. Unanchored, it matches at any depth and would hide `control/PAUSED`. Add a comment saying why, matching the existing header comment's tone (the header already explains root-anchoring; this line simply escaped it).
2. **`control/PAUSED`** — tracked. Present on `main` ⇒ no writes, exactly like the local file today. Also honor repo variable `FFL_PAUSED=1`. Committing a file from the GitHub mobile web UI is the phone kill switch.
3. **`control/lineup.json`** — the manual override, replacing SSH-written `lineup.json`. **Freshness must come from the git commit time** (`git log -1 --format=%cI -- control/lineup.json`), never mtime — see [§2.1 blocker 4](#21-four-blockers-found-in-the-current-code). `load_manual_override()` (`set_lineup.py:158`) changes accordingly; local `lineup.json` keeps working with mtime for the legacy Mac path.

   **The workflow MUST check out with `fetch-depth: 0`.** `actions/checkout` defaults to depth 1, where the single grafted commit is treated as a root and `git log -1 --format=%cI -- <path>` returns **HEAD's** timestamp for every tracked file — so every override looks permanently fresh. Reproduced locally: full clone returns the file's real commit date, depth-1 clone returns the tip's. If the lookup cannot be made reliable, `load_manual_override()` must **fail closed** (treat the override as stale) rather than open.

**Priority — and a safety regression this file introduces.** The chain stays **manual > fresh advice > optimizer**, and manual stays authoritative even when nothing validates. On the Mac that was safe because `lineup.json` is untracked and self-expires by mtime in 20 h. **A committed `control/lineup.json` does not expire** — it persists until a human deletes it. Verified by executing the real `decide()`: an override naming a player no longer on the roster returns `[]` with `source: manual`, leaving an **OUT starter in the lineup indefinitely** while the optimizer never runs.

   Task 1.7 must close this: the git-commit-time freshness window (`lineup_json_max_age_hours`, default 20) applies to `control/lineup.json` exactly as mtime did locally, and an override that is fresh but validates **zero** entries must alert loudly — it means the human's intent no longer matches the roster.

**Accept:** tests for (a) committed PAUSED suppressing writes, (b) `FFL_PAUSED` env, (c) git-commit-time freshness including a stale-commit rejection, (d) precedence unchanged. Suite green.

### 1.6 — Window gate

Add `should_run_now(now=None, mode=None) -> (bool, reason)` in a new `ffl_schedule.py`, using `zoneinfo.ZoneInfo("America/New_York")`. Windows are declared in **ET** and compared against ET, so the **2026-11-01 DST change needs no gate edit**. `FFL_FORCE=1` bypasses.

**Declare the windows explicitly — do not leave them to the implementing agent.** These are the single most consequential runtime parameter in the plan:

| Mode | ET windows |
|---|---|
| `optimize` | Thu 16:00–17:30 · Sun 09:00–10:30 |
| `safety` | Thu 16:00–20:00 · Sat 11:00–20:00 · Sun 07:00–20:05 · Mon 16:00–20:00 |

`optimize` reproduces today's Mac behaviour exactly. `safety` spans the game days and stops 10 minutes before each slate's lock. Until Task 4.1 ships, `safety` is unreachable and `optimize` is the only mode.

**The cron envelope in Task 2.1 must be a strict superset of these windows in BOTH EDT and EST** — the gate cannot fire a job the cron never scheduled. Verify by enumeration, not by inspection.

The gate runs **before any Yahoo call** so out-of-window firings cost ~1 s and zero API quota.

**Accept:** tests with a frozen clock on both sides of 2026-11-01, a UTC-midnight-crossing case, and an enumeration test asserting every window minute above is covered by some cron firing in both DST regimes.

### 1.7 — The executor entrypoint (and the idempotency fixes)

**No task in the original plan created the thing the workflow runs.** Create `ffl_execute.py` at the repo root — a flat module, matching `ffl_config.py`/`ffl_schedule.py`; there is no `ffl/` package and none is needed. It is invoked as `python ffl_execute.py`.

It must, in order:
1. `ffl_schedule.should_run_now()` → exit 0 silently if outside the window (no Yahoo call, no ntfy, no healthchecks ping).
2. PAUSED gate: `control/PAUSED` present **or** `FFL_PAUSED=1` → report planned swaps only, zero writes (N3).
3. Emit `::add-mask::` for every token the moment `TokenStore` loads or refreshes it (N10).
4. Delegate to `set_lineup.api_main()` — **do not reimplement any decision logic** (N2).

And it must fix the three N12 defects, none of which the frozen code handles:
- **Locked-starter loop:** skip pass-1 candidates whose outgoing starter is `locked`. The swap is impossible; retrying it every 20 minutes produces an endless `PARTIAL` + ntfy loop. This changes `plan_swaps()` — permitted, and the narrow exception to §17's freeze.
- **Non-expiring committed override:** apply the freshness window to `control/lineup.json` (Task 1.5), and alert when a fresh override validates zero entries.
- **`max_swaps_per_run`:** bound the apply loop (N14).

**Accept:** unit tests for each of the three defects (the locked-starter case must converge to `[]` on run 2, not repeat); window-gated exit costs zero Yahoo calls; `python ffl_execute.py` with `DRY_RUN=1` reproduces `api_main()`'s planned swaps exactly.

---

## 8. Phase 2 — The GitHub Actions executor

**Goal:** scheduled, deterministic, unattended execution. Ship dry first.

### 2.1 — `.github/workflows/lineup.yml`

```yaml
on:
  schedule:            # UTC — blanket envelope; ffl_schedule.should_run_now() decides
    - cron: "*/20 20-23 * * 1,4"     # Thu + Mon evening ET (TNF, MNF)
    - cron: "*/20 15-23 * * 6"       # Sat ET (late-season Saturday slate)
    - cron: "*/20 11-23 * * 0"       # Sun ET (intl → early → late → SNF)
    - cron: "*/20 0-1  * * 0,1,2,5"  # spillover past UTC midnight for each of the above
  workflow_dispatch:
    inputs:
      dry_run: {type: choice, options: ["1", "0"], default: "1"}
      force:   {type: choice, options: ["1", "0"], default: "1"}
      mode:    {type: choice, options: [optimize, safety], default: optimize}
concurrency:
  group: ffl-executor
  cancel-in-progress: false      # never two runs racing a token refresh
env:
  # FAIL-SAFE DEFAULT. `inputs.*` is populated ONLY for workflow_dispatch; on a
  # `schedule` event it is the empty string. Defaulting to "1" here means a
  # scheduled run is DRY unless the repo variable FFL_LIVE is explicitly "1".
  DRY_RUN: ${{ inputs.dry_run || (vars.FFL_LIVE == '1' && '0' || '1') }}
```

Job steps: `actions/checkout` **with `fetch-depth: 0`** (Task 1.5 — depth-1 breaks commit-time freshness) → `setup-python@v5` (3.12) → **no pip install** (N13, stdlib only) → extract advice (`git show origin/main:advice/lineup.json > advice_remote.json`, merge-free, mirroring `run.sh:19`) → `python ffl_execute.py` (Task 1.7) → redact-scan the state dir, failing the job on a token-shaped hit (N10) → healthchecks ping (`$HC` / `$HC/fail` on nonzero) → `actions/upload-artifact`.

**The cron envelope is verified by enumeration, not inspection.** The four expressions above yield 114 firings/week and cover every ET window in Task 1.6 under **both** EDT and EST. The obvious-looking set does not: a naive `*/20 21-23 * * 1` for MNF covers Monday *afternoon* ET and never reaches the 20:15 ET lock, which lands on **Tuesday** UTC. Re-run the enumeration after any cron edit.

**Why blanket cron + a Python gate, not precise cron:** GitHub's scheduler is best-effort and **routinely delayed 10–30 minutes**, sometimes far more, with no SLA. A single precise firing at lock-minus-45 is a coin flip. Twenty cheap firings across a window mean a delayed one is simply the *next* one, and N12 idempotency makes the redundancy free. Also note GitHub disables scheduled workflows after 60 days of repo inactivity — the advice commits keep it alive, but the workflow should log a warning if the last run was >30 days ago.

**Accept:** a `workflow_dispatch` run with `dry_run=1, force=1` (force is required — an ad-hoc run is almost always outside a window, and the gate would otherwise exit silently) produces a `NO_CHANGE` `[DRY] planned: …` status with `dry_run: true` in `last_status.json`, artifact uploaded, ntfy received, healthchecks pinged. Zero writes. Separately assert a **scheduled** run is dry: trigger one with `vars.FFL_LIVE` unset and confirm `dry_run: true`.

### 2.2 — Token durability · **mandatory regardless of 0.4's verdict**

Task 0.4's verdict sets urgency, not necessity — Yahoo may rotate at any refresh (see 0.4). Implement write-back, choosing the store at Gate G2:
- **(a) GitHub secret write-back** — `gh secret set YAHOO_REFRESH_TOKEN` using a fine-grained PAT scoped to *this repo only*, permissions **Secrets: write + Contents: read**, stored as `GH_SECRETS_PAT`. Simple; blast radius is a PAT that can rewrite this repo's secrets.
- **(b) External KV** — e.g. Render Key Value, which the owner already has infrastructure for. Keeps GitHub out of the credential-rotation loop and avoids a secrets-write PAT.

Write back **only when the token actually changed**.

**Where the write-back runs matters, and both obvious placements fail.** `finish()` ends in `sys.exit(code)` (`set_lineup.py:66`) and *every* terminal branch of `api_main()` calls it (`set_lineup.py:469–476`), so code placed after `api_main()` never executes. Put the persistence in `TokenStore._refresh()` itself (`yahoo_api.py:158–162`), immediately after the new token is accepted and before any further work — that is the only point guaranteed to be reached, and it is already inside the flock.

**On the `concurrency` group:** it does prevent two *same-repo scheduled* runs from refreshing simultaneously — a queued run waits rather than being discarded. It does **not** cover a run racing a local Mac invocation or a connector container refreshing the same credential. If the connector ships (Phase 5), it holds a second copy of the same refresh token and can rotate it out from under the executor — see 5.3.

**Accept:** a forced rotation is persisted and the *next* run authenticates with the rotated token; a run that exits via `finish()` on the `PARTIAL` path still persisted it. Test with mocked HTTP.

### 2.3 — Observability

- ntfy push per acting run (already in `finish()`); silent for out-of-window no-ops.
- healthchecks.io: ping only from runs that **completed the decision cycle**, not from window-gated exits — otherwise the dead-man check is meaningless. Configure the check's schedule against window close, grace 45 min.
- Log scheduled-vs-actual firing delta each run and append to `docs/cron_delay_log.md`. This produces the data for Gate G5.

**Accept (blocking):** the delay-logging step and the healthchecks ping are wired; one run appends a row to `docs/cron_delay_log.md`; the dead-man alert fires when a window is deliberately skipped.

**Ongoing (NOT blocking — proceed to 2.4 immediately):** delay data accumulates across Phase 2 and is read at Gate G5. §14's serial-execution rule does not mean waiting two calendar weeks here.

### 2.4 — Dry-run soak · **one full game week**

`DRY_RUN=1` for an entire week (Thu → Mon), zero writes.

**Do not compare against the Mac — that baseline does not exist.** Verified: `launchctl list | grep ffl` returns nothing, `logs/` holds only `.gitkeep`, and `config.json` still carries `league_id`/`team_id` = `REPLACE_ME` with `write_path: browser`. The Mac executor has never completed a run and has no `last_status.json` to diff against.

Compare against a **deterministic replay** instead: for each Actions run, re-execute the same decision locally against the roster snapshot that run captured (`api_roster_before_*.json` from its artifact) and assert the planned swaps are byte-identical. This tests exactly what 2.4 is for — that config resolution (Task 1.2) and the window gate (Task 1.6) did not change the decision — without depending on a machine that was never armed.

**Accept:** full week, zero writes, every run's planned swaps reproduce under local replay, no missed window. → **Gate G4**

### 2.5 — First supervised live run · *human watching*

`workflow_dispatch` with `dry_run=0`, outside 3 h of any lock, owner watching. Confirm `SUCCESS`/`NO_CHANGE`, the Yahoo iOS app reflects the change, ntfy arrives.

**Accept:** one clean live Actions write.

### 2.6 — Two clean scheduled windows

Two consecutive real windows executed by Actions, **Mac powered off**.

**Accept:** two clean windows, healthchecks green, statuses correct. → **Gate G5**

---

## 9. Phase 3 — Retire the Mac and the browser

Only after **G5**. Do not start early.

### 3.1 — Disarm the Mac
`./teardown.sh` (re-enables sleep, removes both LaunchAgents). Verify `pmset -g | grep SleepDisabled` → `0` and `launchctl list | grep ffl` → empty. Keep the clone as a manual runner.

### 3.2 — Delete the browser path
Remove `browser_path.py`, `seed_login.py`, `profile/`, `screenshots/`, `run.sh`, `arm.sh`, `smoke_test.sh`, `teardown.sh`, `launchd/`, and `playwright` from `requirements.txt`. Drop `write_path` config entirely — there is one path now. Git history is the archive; do not keep dead code "just in case."

Two details that will break the build if missed:
- **`install.sh` has two Playwright references, not one** — the browser download at `install.sh:14` *and* the import check at `install.sh:26`. The script runs under `set -euo pipefail` (`:3`), so leaving `:26` makes `./install.sh` exit non-zero on a fresh clone the moment Playwright leaves `requirements.txt`.
- **`.github/workflows/tests.yml:20` hard-references four files this task deletes.** Reduce it to `bash -n install.sh`. (Note the existing line `bash -n run.sh install.sh …` never actually checked five files — `bash -n` treats operands after the first as positional parameters — so this is a correctness fix, not just a cleanup.)

### 3.3 — Rewrite the docs
README §1.1's diagram, §1.5 (Swap Mode), §2 steps 3–9, §3.1's browser failure modes, and §3.3's triggers are all obsolete. Rewrite around the shipped architecture. `docs/UPGRADE_STATUS.md` gets a final state.

**Accept:** suite green with no Playwright installed anywhere; README describes only what exists; a fresh clone + `pip install -r requirements.txt` is a no-op beyond dev tools.

---

## 10. Phase 4 — Expand coverage

The capability the migration unlocks (see [§2.2](#22-coverage-gap-the-migration-should-close)). **Optional but recommended.** Do not start before G5.

### 4.1 — Two modes

Running the full optimizer every 20 minutes all Sunday would churn as projections drift; `min_swap_gain` alone is not enough protection. Split:

- **`safety` mode** — pass 1 only: replace starters who are BYE/OUT/IR/SUSP/inactive. Safe to run continuously across all game days **once Task 1.7's locked-starter fix is in**. Without it, "never churns" is false: pass 1 does not check `st["locked"]` (only pass 2 does, `set_lineup.py:270`), so a locked OUT starter regenerates the same impossible swap on every firing — verified, identical output on runs 1/2/3.
- **`optimize` mode** — full `decide()` including advice and pass-2 upgrades. Runs only in the two primary windows (Thu ~16:00–17:30 ET, Sun ~09:00–10:30 ET), preserving today's behavior exactly.

Implement as a parameter to the existing functions — **do not fork `plan_swaps()`**. N2 says `decide()` is the single source of lineup logic.

**Accept:** mode tests; `optimize` reproduces today's decisions bit-for-bit on the fixtures.

### 4.2 — Per-slot lock awareness
Derive kickoff/lock times per player (Yahoo API, else a schedule source) and act at lock-minus-N for **every** slot: TNF, Sun early/late, SNF, MNF, Saturday. Enforce N14's swap budgets.

**Accept:** a simulated Sunday shows a 16:25 player ruled out at 15:00 being replaced — the case today's system structurally cannot handle.

### 4.3 — Quota guard
Blanket cron on a private repo consumes minutes (2,000/mo free). The corrected cron set in 2.1 fires **114×/week**, and GitHub bills each job **rounded up to the whole minute** — so a 3-second window-gated exit still costs 1 minute. Realistic: ~114 min/week ≈ **500 min/mo**, a quarter of the free tier rather than the 260 min an earlier draft estimated from wall-clock. Comfortable, but add a job-level `timeout-minutes: 5` and log cumulative usage monthly. If the margin ever tightens, widen the cron interval to `*/30` before trimming windows.

---

## 11. Phase 5 — Connector + Claude layer

**Deliberately last.** The executor never touches the connector; only Claude does. Sequencing it after Phase 3 means an auth dead-end here cannot block the migration.

### 5.1 — Auth path viability · **check before building**

The v1 plan assumed "no-auth server + unguessable URL" as the claude.ai-compatible path. Two open issues bear on this:
- [anthropics/claude-ai-mcp#112](https://github.com/anthropics/claude-ai-mcp/issues/112) — the Add-connector UI exposes only OAuth client id/secret; **no bearer/header field**. Confirmed still accurate in 2026.
- [anthropics/claude-ai-mcp#402](https://github.com/anthropics/claude-ai-mcp/issues/402) — *"Custom connector flow fails on unauthenticated remote MCP servers — no 'no auth' option in admin UI."*

If #402 applies to this account, **both** documented paths are closed and the connector needs real MCP-spec **OAuth 2.1** — a substantially bigger build than the v1 plan's "fallback task" framing implies. Test the actual account's Add-connector UI *before* writing deployment code.

**Accept:** documented answer to "which auth forms does this account's UI accept?" → **Gate G6**

### 5.2 — License decision · *resolves v1 `P3.T1`, currently OPEN*

Verified 2026-08: both [derekrbreese/fantasy-football-mcp-public](https://github.com/derekrbreese/fantasy-football-mcp-public) and [cketcham/fantasy-football-mcp](https://github.com/cketcham/fantasy-football-mcp) are **MIT**. Patterns and code may be adapted **with attribution**; no clean-room requirement. Record in `connector/README.md` and flip `P3.T1` to DONE in `docs/UPGRADE_STATUS.md`.

Note for scoping: **every published Yahoo FF MCP server is read-only.** derekrbreese's `ff_build_lineup` recommends; it does not submit. There is no prior art for the write tool — and correspondingly no prior art proving it safe. This is further support for N8/N9.

### 5.3 — Deploy
Containerize (`connector/Dockerfile` exists). Host per Gate G6 — Render (owner has existing infrastructure), Fly.io, or Cloudflare Workers. **Render free tier spins down after ~15 min idle with a ~50 s cold start, which will time out an MCP handshake** — budget for a paid instance or a keep-warm ping.

Smoke **must** use the exact URL form claude.ai will use, not just an `Authorization` header — a header-only smoke passes while the real form 401s.

**Credential collision — resolve before deploying.** `connector/server.py` reads Yahoo tokens from `$YAHOO_SECRETS_PATH`, i.e. deploying puts a **second copy of the same refresh token** on a third-party host. If Yahoo rotates on refresh (0.4), the connector and the executor will rotate each other's credential out from under one another and the executor locks out at a lock window. Either give the connector its own Yahoo app + token, or make it read-through a single shared store (the Gate G2 KV), or restrict it to a read-only token. Do not deploy with a naive copy.

**Accept:** public HTTPS URL serving MCP; `tools/list` and `ff_get_roster` succeed over the exact claude.ai URL form; `ENABLE_WRITES` unset and verified refusing; no Yahoo token reachable in any response; the credential-collision decision recorded in `connector/README.md`.

### 5.4 — Wire into Claude
Add the connector at claude.ai → Settings → Connectors. Recreate both Routines carrying it. Rewrite `docs/ROUTINE_PROMPT.md` to v2: **PRIMARY** = connector tools (live roster *with slot assignments*), **SECONDARY** = web research, **FALLBACK** = `roster.json`. The "you do not know the current slot assignments" rule (`ROUTINE_PROMPT.md:63`) and its start-X-over-Y guessing workaround get **deleted** — the connector makes them obsolete. `roster.json` is demoted to fallback and stops needing manual upkeep after add/drops.

The advice output contract (`advice/lineup.json` shape, `generated_at` freshness, commit-to-`main`) is **UNCHANGED** so the executor needs no modification.

**Accept:** a fired Routine reads the live roster and commits advice naming actual current starters.

---

## 12. Phase 6 — Gated optional tracks

Neither is required for the definition of done. Both default **off**.

### 6.1 — Cowork viability spike · *investigation only*
Unknown capabilities as of this writing. Answer empirically, build nothing: (a) can it run on a schedule? (b) can it reach `ntfy.sh` / `hc-ping.com`, or does the same egress proxy that blocks the Routines apply? (c) can it complete a git push unattended? (d) can it hold connectors?

**Create no dependency on Cowork until all four are answered.** Record in `docs/ENHANCEMENT_PLAN.md`.

### 6.2 — Claude as tier-2 executor · **only if Gate G5 failed**
If Actions proves unreliable, enable `ff_set_lineup` with `confirm=true` as a *manual* fallback the owner invokes from chat. Requires Gate G7. `ROUTINE_PROMPT.md` must explicitly forbid Routines from calling it autonomously.

This contradicts N9 and is therefore owner-decision-only. **Do not implement pre-emptively.**

---

## 13. Decision gates

Stop and ask the human at each. Never self-approve.

| Gate | After | Question | If it fails |
|---|---|---|---|
| **G0** | 0.5 | Did the `fspt-w` write + revert succeed on the real league? | Stop the write path. Read-only connector only; lineup stays manual. |
| **G1** | 0.6 | Does Yahoo serve authenticated reads from GitHub runners without 999 — and is any block IP-scoped or app-scoped? | If app-scoped: cut firing frequency / cache the roster read per window; relocating will not help. If genuinely IP-scoped: self-hosted Pi, or Render/Fly cron. |
| **G2** | 0.4 | Refresh token STABLE or ROTATES — and if rotates, GitHub secret write-back or external KV? | — |
| **G3** | **before 0.6** | Make the repo private before Actions holds Yahoo credentials? | Public repo also leaks lineup strategy hours before lock (README §1.5b). Private costs a PAT for Routine pushes and meters Actions minutes. **Recommend private.** |
| **G4** | 2.4 | Did every dry run's planned swaps reproduce under deterministic local replay? | Root-cause the divergence — it means config resolution or the window gate changed a decision. Do not cut over. |
| **G5** | 2.6 | Two clean live windows with the Mac off? | Do not delete the browser path (Phase 3). Consider §6.2 or a self-hosted runner. Note the Mac has never been armed, so it is not a standing fallback — arming it is itself a task. |
| **G6** | 5.1 | Which connector auth form does this account accept? | If none: build MCP OAuth 2.1, or drop the connector — the executor does not need it. |
| **G7** | 5.4 | Enable connector writes? | **Default NO, forever.** See N8/N9. |

---

## 14. Execution protocol

- **Branch:** work on `claude/cloud-native-v2`. The Routines commit `advice/*` directly to `main` on their own schedule, so the branch and `main` **will** diverge mid-plan. Before every push: `git fetch origin main && git merge origin/main` into the branch, push the branch, then `git checkout main && git merge <branch> && git push`. A merge commit is fine — **do not** rely on `--ff-only`.
- **Workflows are the exception to the branch rule.** GitHub runs `schedule:` **only** on the default branch, and `workflow_dispatch` will not appear in the UI or accept an API dispatch unless the workflow file exists on the default branch. `probe.yml` (0.6) and `lineup.yml` (2.1) must therefore be merged to `main` to be exercised at all — which is safe only because the fail-safe `DRY_RUN` default in 2.1 means a merged-but-unfinished workflow cannot write.
- **Never edit the armed clone.** If the Mac is ever armed, `launchd` executes `$HOME/ffl-agent`'s working tree directly, so a half-refactored checkout becomes the thing that runs at lock time. Do plan work in a separate worktree (`git worktree add ../ffl-agent-cloud claude/cloud-native-v2`).
- **Order:** execute tasks in order; respect gates absolutely.
- **Before every commit:** `.venv/bin/pytest tests/` (all green), `pyflakes` on changed Python, `bash -n` on changed shell.
- **Never fabricate acceptance.** Paste real command output when reporting. A task is not done because the code looks right.
- **Never commit:** tokens, client secrets, `~/.ffl-secrets` contents, unsanitized API payloads, `.env`. New runtime state files go in `.gitignore` **root-anchored** — and note that `.gitignore:5` currently violates its own header comment (Task 1.5).
- **Commit style:** imperative summary + body explaining *why*. No model names in commits.
- **Owner-account tasks** (0.1, 0.2, live runs of 0.3/0.5, 2.5, 5.1, 5.4) need the owner's Yahoo tokens or claude.ai account. Author the code and exact commands, then hand off. **Never move tokens into a cloud session** (N5).
- **If live reality contradicts this plan** — field names, XML shape, scope behavior, connector auth — trust the live system, fix minimally, and update this file plus `docs/api_notes.md` in the *same* commit.

---

## 15. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Yahoo 999-blocks GitHub runner IPs** | Medium | Gate G1 proves it in an hour. Fallback: self-hosted Pi (README §3.3 pre-plans it) or Render/Fly. |
| **GitHub cron delay misses a lock** | **High** (10–30 min is routine, no SLA) | Blanket cron every 20 min + idempotent runs (N12) + window gate. Delay logged for G5. |
| ~~`player_key` vs `player_id` rejected by the PUT~~ | **Not a risk** | Retracted: `yahoo_fantasy_api/team.py:492` builds `<player_key>`; the `player_id` in its signature is a Python arg name, not the XML element. `yahoo_api.py:336` is already correct. |
| Yahoo denies Read/Write at app creation | Low (no reports of it) | Gate G0 catches it in the first hour; degrades to read-only. |
| Refresh token rotates + write-back fails → lockout | Medium | Task 0.4 determines it up front. Write back only on change; `concurrency` prevents races; `AuthExpired` → exit 2 + urgent ntfy, recovery is one `yahoo_auth.py` run. |
| Secrets-write PAT leaks | Low / high impact | Avoid entirely if 0.4 says STABLE. Else fine-grained, single-repo, two permissions — or option (b), external KV. |
| Secret leaks into Actions logs | Medium | N10: `::add-mask::` on *derived* values, which GitHub does not mask automatically. Test asserts clean `last_status.json`. |
| Projections absent from the API | Medium | `proj=0.0`, `PROJ_AVAILABLE=False` → optimizer degrades to pass 1 (still fixes BYE/OUT). Advice carries projections. |
| Churn from frequent runs | Medium | `safety` vs `optimize` modes (4.1) + `min_swap_gain` + N14 swap budgets. |
| claude.ai rejects both connector auth forms (#402) | Medium | Gate G6 checks *before* building. Connector is optional; executor never depends on it. |
| Render free-tier cold start times out MCP handshake | High if free tier used | Budget a paid instance or keep-warm ping. |
| Losing the residential-IP advantage | — | Only mattered for the browser path, which Phase 3 deletes. |
| Both GitHub and the Mac unavailable | Low | Owner sets the lineup in the Yahoo app. The system is an optimization, not a dependency — keep it that way. |

---

## 16. Relationship to the v1 plan

`docs/API_CONNECTOR_UPGRADE_PLAN.json` stays in the repo and stays authoritative for **Yahoo API internals and connector tool design**. This file replaces its *execution model*.

| v1 | Disposition here |
|---|---|
| `P0.T1–T4` (dev app, auth, probe, write proof) | **Absorbed** → §6 tasks 0.1–0.5, plus the new 0.4 and 0.6 |
| `P1.*` (yahoo_api.py) | **Done.** No change. |
| `P2.T1–T3` (write_path switch, api branch, docs) | **Done.** Phase 3 removes the switch — one path survives. |
| `P2.T4–T5` (live runs on the Mac) | **Superseded** by §8 2.5/2.6, which run on Actions instead |
| `P3.T1` license check | **Resolved** — both reference repos MIT (§11 5.2) |
| `P3.T2–T5` connector | **Kept**, resequenced last (§11) |
| `P4.*` Routines + connector | **Kept** (§11 5.4) |
| `P5.*` transition/decommission | **Superseded** by §9 Phase 3 — the Mac is retired outright, not reduced to a scheduler |
| Gates G0–G3 | G0 kept; G1→G5; G2→G6; G3→G7. New: G1 (datacenter IP), G2 (token durability), G3 (repo privacy), G4 (shadow week). |

---

## 17. Quick reference — what dies, what lives

**Dies:** Playwright · Chromium · `seed_login.py` · `profile/` · the three pinned DOM selectors · `screenshots/` · `launchd/` · `run.sh` · `arm.sh` · `smoke_test.sh` · `teardown.sh` · `pmset disablesleep` · `caffeinate` · the residential-IP requirement · "the Mac must be awake" · README §2 steps 3–9.

**Lives, semantically unchanged:** `decide()` · `plan_swaps()` · `build_override_swaps()` · `startable()` · `eligible()` — Task 1.7 adds the locked-starter guard to pass 1 and Task 4.1 adds a `mode` parameter; `optimize` mode must reproduce today's decisions bit-for-bit on the fixtures. Everything else · the `finish()` status/exit-code contract · `yahoo_api.py` · `ffl_common.py` · the `advice/` data channel and its `generated_at` freshness rule · every guardrail in N1–N8 · the 82 tests.

**New:** `.github/workflows/lineup.yml` · `.github/workflows/probe.yml` · **`ffl_execute.py`** (the entrypoint — Task 1.7) · `ffl_config.py` · `ffl_schedule.py` · `browser_path.py` (Phase 1, deleted again in Phase 3) · `config.defaults.json` · `control/PAUSED` · `control/lineup.json` · `scripts/yahoo_token_probe.py` · `tests/fixtures/roster_live.json` · `docs/api_notes.md` · `docs/cron_delay_log.md`.

**The test of success:** unplug the MacBook, close it, put it in a drawer. Thursday's lineup still gets set.
