# FFL Agent — Yahoo Fantasy Football Auto-Lineup ("A2: Lid CLOSED" path)

Sets your Yahoo Fantasy Football lineup automatically before Thursday and Sunday locks, from an Apple Silicon MacBook that stays **lid-closed on AC power** all season. Pure Playwright (no Claude at runtime) · headless Chromium · launchd. The iPhone is the monitor/recovery console (ntfy alerts + SSH); it has no execution role.

---

## 1. System walkthrough

### 1.1 Big picture

```
Claude Code Routines (cloud · fire into the operator session · optional layer)
│    Thu 20:00 UTC & Sun 13:00 UTC — research injuries/weather/Vegas/consensus
│    → commit advice/lineup.json + rationale to main → push notification (veto window)
▼
GitHub repo (data channel + season memory in advice/history/)
▼
MacBook (Apple Silicon · AC power · lid CLOSED · pmset disablesleep=1)
│
├─ launchd LaunchAgents (Mac local time = ET in Miami)
│    ├─ com.nathaniel.ffl.thu   → Thu 17:30  (≈2h45m before TNF lock)
│    └─ com.nathaniel.ffl.sun   → Sun 10:30  (≈2h30m before early locks)
│         │
│         └─ run.sh
│              ├─ advice fetch: git fetch + git show → advice_remote.json
│              │            (never merges; offline keeps previous extract)
│              ├─ sentinel: SleepDisabled==1 ? (warn via ntfy if not)
│              ├─ caffeinate -i  .venv/bin/python set_lineup.py
│              │     ├─ 0 PAUSED file present ? → report planned swaps
│              │     │            only ([PAUSED]), zero writes, exit 0
│              │     ├─ 1 READ    headless persistent Chromium (./profile)
│              │     │            login-guard → abort+notify if sign-in page
│              │     │            parse roster: slot, player, status, bye,
│              │     │            Yahoo projected pts, locked flag
│              │     ├─ 2 DECIDE  manual lineup.json > fresh Routine advice
│              │     │            > greedy optimizer (eligibility map,
│              │     │            never BYE/OUT, min_gain threshold)
│              │     ├─ 3 WRITE   Swap Mode: click starter slot →
│              │     │            click highlighted bench player,
│              │     │            re-parse after every swap
│              │     ├─ 4 VERIFY  final roster == target; PNG before/after
│              │     └─ 5 REPORT  last_status.json + ntfy push
│              └─ healthchecks.io ping (…/fail on nonzero) → dead-man alert
│
└─ iPhone = monitor/recovery console: ntfy app (alerts), Termius (SSH
   re-run / logs), no execution role.
```

**Why it works lid-closed:** `sudo pmset -a disablesleep 1` sets a kernel flag that vetoes sleep even with the lid shut and no display. The Mac stays awake, launchd fires on time, and **headless** Chromium needs no display. (Headed would fail — no display exists lid-closed.) This satisfies "MacBook closed", not "MacBook off" — a scheduled cold boot is impossible on Apple Silicon.

**Why it's fragile (and how that's covered):** the flag is undocumented; macOS updates/reboots can reset it; critical battery forces sleep; after a reboot, user LaunchAgents don't run until you log in (FileVault). Every run therefore starts with a sentinel that warns over ntfy if the flag was reset, and healthchecks.io fires a dead-man alert whenever a scheduled run doesn't ping.

**Runtime dependencies:** Python + Playwright Chromium only. Zero Claude, zero Anthropic auth, zero MCP at runtime. Claude is optional at *decide*-time via the `lineup.json` handoff (§1.4).

### 1.2 Components, in execution order

| Component | What it does |
|---|---|
| `launchd/*.plist` | Two LaunchAgents fire `run.sh` Thu 17:30 and Sun 10:30 (Mac local clock). Committed copies reference `~/ffl-agent`; `arm.sh` rewrites them to the clone's real path at install time. |
| `run.sh` | Entrypoint: extracts the latest committed advice (`git fetch` + `git show`, merge-free, `GIT_TERMINAL_PROMPT=0` so a missing credential fails fast instead of hanging), warns via ntfy if `SleepDisabled` got reset, runs the agent under `caffeinate -i`, pings healthchecks (`…/fail` on nonzero exit), rotates logs (keep 30) and screenshots (keep 40). |
| `set_lineup.py` | The agent — the five stages in the diagram. Everything below (§1.3–§1.6) is its contract. |
| `seed_login.py` | One-time, lid-open, **headed** login that seeds `./profile` (persistent Chromium cookies). The agent never touches credentials — it only reuses this profile. |
| `install.sh` / `arm.sh` / `smoke_test.sh` / `teardown.sh` | Setup, arming, lid-closed smoke test, and full revert — used in §2. |
| `yahoo_api.py` + `ffl_common.py` | Yahoo Fantasy API client (OAuth2 reads + position-only lineup writes) and the shared name-matching helpers — used by the api write path, the `scripts/` tooling, and the connector. |
| `scripts/` | `yahoo_auth.py` (one-time OAuth bootstrap), `yahoo_probe.py` (roster dump + unknown-field resolution), `yahoo_write_proof.py` (supervised benign write+revert — gate G0). |
| `connector/` | Custom Claude connector: remote MCP server (streamable HTTP, bearer auth) exposing live roster/matchup/free-agent tools; write tool disabled by default. See `connector/README.md`. |
| `roster.json` | Your roster, names exactly as Yahoo renders them — the research Routines' input; edit after every add/drop. |
| `league_settings.json` | Slots/eligibility/scoring for the Routines (`config.json` is gitignored so the cloud can't read it; keep the two eligibility maps in sync). |
| `advice/` | Routine-committed `lineup.json` + `history/` rationale archive — the data channel and season memory (see §1.5b). |
| `docs/ROUTINE_PROMPT.md` | Versioned operating instructions the research Routines follow; `docs/ENHANCEMENT_PLAN.md` holds the design rationale. |
| `tests/` | Browser-free unit tests over the decision logic; run by CI (`.github/workflows/tests.yml`). |

### 1.3 Data files and their schemas

**`config.json`** (copy of `config.example.json`, gitignored — your real ids/URLs never get committed):

| Field | Meaning |
|---|---|
| `league_id`, `team_id` | From your team URL: `https://football.fantasysports.yahoo.com/f1/<league_id>/<team_id>` |
| `min_swap_gain` | Bench must project at least this many points above a healthy starter to justify a swap (anti-churn threshold; default `1.0`) |
| `lineup_json_max_age_hours` | Manual-override freshness window (default `20`) — older `lineup.json` is ignored |
| `advice_max_age_hours` | Routine-advice freshness window (default `6`), measured against the advice file's embedded `generated_at` |
| `ntfy_topic` | Random topic name; subscribe to it in the iPhone ntfy app |
| `healthchecks_url` | Ping URL of your healthchecks.io check |
| `slot_eligibility` | Map of **primary position → slots that position may fill**, e.g. `"RB": ["RB", "W/R/T"]`. Adjust for your league's flex types (superflex: add `"Q/W/R/T"` entries) |
| `bad_statuses` | Statuses that are never startable: `["O","IR","SUSP","NA","PUP","NFI"]` (`Q` and `D` remain startable) |

**Override sources** — the DECIDE stage consumes two override files through one validation path (normalized name matching, startable/unlocked/eligible checks, swap-in must currently be on the bench, each player in at most one swap), with different trust levels:

- **`lineup.json`** (gitignored, human-written via SSH; format in `lineup.sample.json`): `{"swaps": [{"out": "...", "in": "..."}]}`. Fresh by file mtime (< `lineup_json_max_age_hours`, default 20) and containing at least one entry → **authoritative**: the optimizer does not run, even if no entry validates — it can't churn a lineup you set deliberately. Note `{"swaps": []}` counts as *no override* (the optimizer runs); to freeze the lineup entirely, use `touch PAUSED`, not an empty swaps file.
- **`advice/lineup.json`** (tracked, committed by the research Routines; extracted by `run.sh` into gitignored `advice_remote.json`): same `swaps` shape plus a required `generated_at` ISO-8601 timestamp. Freshness comes from `generated_at` (< `advice_max_age_hours`, default 6) — never mtime, which git extraction resets — and future-dated timestamps beyond a 1-hour clock-skew tolerance are rejected too (a wrong future date would otherwise stay "fresh" for weeks). Fresh advice whose entries **all** fail validation is treated as garbage research and the optimizer runs instead.

Priority: manual `lineup.json` > fresh advice > optimizer. `last_status.json` reports which source ran as `source: manual|advice|optimizer`, and any rejected override entries appear with a reason under `override_skipped` and in the ntfy summary — a discarded recommendation is never silently dropped.

**`logs/last_status.json`** (written by every run):

```json
{"status": "SUCCESS", "ts": "20260910T173012", "dry_run": false,
 "summary": "RB: Player A → Player B (+3.5)", "source": "optimizer"}
```

`status` ∈ `SUCCESS` · `NO_CHANGE` · `PARTIAL` · `LOGIN_REQUIRED` · `ERROR`. The same summary goes out as the ntfy push. Exit codes: `0` ok/no-change/partial-with-progress, `2` login required, `3` write error (no swaps applied), `4` parse error.

**Other state:** `profile/` (persistent Chromium cookies — never commit), `screenshots/before_*.png` / `after_*.png` (evidence per run), `PAUSED` (touch to disable writes instantly, see §1.6).

### 1.4 The DECIDE stage

1. If a **fresh manual `lineup.json`** exists, use it (validated, authoritative — see §1.3).
2. Otherwise, if **fresh Routine advice** exists and at least one entry validates, use it.
3. Otherwise the **greedy optimizer**, two conservative passes:
   - **Pass 1 — never start a dead slot:** every unstartable starter (BYE or bad status) is replaced by the highest-projected startable, unlocked, slot-eligible bench player.
   - **Pass 2 — upgrades over the threshold:** a healthy, unlocked starter is upgraded only when a bench candidate projects ≥ `min_swap_gain` points higher.
   - Each bench player is used at most once; `Q`/`D` players are startable; locked bench players are never swapped in.

This is deliberately not a global optimum search — it is a conservative, explainable pass that never leaves a BYE/OUT player in and never churns for marginal gains.

### 1.5 The WRITE + VERIFY stages

Writes use Yahoo's **Swap Mode**: click the starter's slot control, then click the highlighted bench player. After every swap the roster is **re-parsed and the swap confirmed** before continuing; after any failure (timeout, verify miss) the page is hard-reloaded so a dangling selection can never turn the next click into an unplanned write, and each swap is preceded by a pre-check that the outgoing starter still holds the planned slot. Before/after full-page PNGs are kept for every run.

> **DOM contract:** three selectors are deliberately loose and tagged `Phase 2: pin` in `set_lineup.py` — the projected-points column index, the slot-control locator, and lock detection. They MUST be pinned against your live roster page (step 5 in §2) before any live write. Everything else is behavior, not guesswork.

### 1.5a Write paths: browser (default) vs api

`config.json` `"write_path"` selects how swaps reach Yahoo. **`browser`** (default) is the original Playwright Swap-Mode path — needs the seeded cookie `profile/`, the pinned DOM contract, and benefits from a residential IP. **`api`** performs the whole READ→WRITE→VERIFY cycle over the Yahoo Fantasy Sports API (`yahoo_api.py`, OAuth2 `fspt-w`): no browser launch at all, no DOM, no cookies, no IP sensitivity — one authenticated PUT per swap, verified by re-reading the roster. The DECIDE stage, guardrails, `DRY_RUN`/`PAUSED` gates, statuses, and exit codes are identical on both paths; on the api path `LOGIN_REQUIRED` (exit 2) means the OAuth refresh died — re-run `scripts/yahoo_auth.py` (not `seed_login.py`). Prerequisite for `api`: gates G0 in `docs/API_CONNECTOR_UPGRADE_PLAN.json` (Yahoo developer app + supervised write proof via `scripts/yahoo_write_proof.py`). Evidence per run: `logs/api_roster_before/after_*.json` replace the PNG screenshots.

### 1.5b The research layer (Claude Code Routines)

Two scheduled Routines fire **into the persistent operator session** (the Claude Code cloud session that built this system) — **Thu 20:00 UTC and Sun 13:00 UTC**, chosen so they land 1.5–2.5h before the Mac runs on both sides of the November DST change. (Fresh-session-per-firing mode was tried first and abandoned: those sessions finished the research but their pushes to `main` were held for a review nobody watches — see `docs/ENHANCEMENT_PLAN.md`. The pipeline as shipped is smoke-validated end to end: fire → advice commit on `main` → `run.sh`-style extraction → freshness parse.) Each firing follows `docs/ROUTINE_PROMPT.md`: read `roster.json` + `league_settings.json` + recent `advice/history/`, research the slate (injuries, confirmed inactives, weather, Vegas context, expert consensus, kickoff locks), then commit `advice/lineup.json` and a rationale file to `main` and end with a summary notification — opening the human **veto window** before the Mac run (do nothing → advice executes; `touch PAUSED` → nothing does; SSH a manual `lineup.json` → the human wins).

While `roster.json` still contains `Placeholder` names, Routines run in **SMOKE MODE**: they push an empty-swaps advice file that validates the pipeline without generating real advice (the executor treats empty swaps as "no advice" and runs the optimizer).

The layer is fail-safe by construction: no commit, a late commit, a wrong timestamp, or hallucinated names all degrade to the optimizer. The Mac never needs an Anthropic key; the cloud never sees Yahoo cookies. **Note:** in a public repo the advice commits reveal your planned lineup hours before lock — consider making the repo private (the Mac then needs a read credential for its fetch).

### 1.6 Hard guardrails (encoded in code, not just documented)

- **Position swaps only** — the agent has no code path that can add, drop, or trade.
- **Never start BYE/OUT/IR/SUSP** — enforced in both optimizer passes *and* the override path.
- **Locked players skipped and reported** — never swapped in; a locked slot's failed attempt is reported, not silently dropped.
- **Zero writes on any sign-in page** — the login-guard runs before anything else; expired session → screenshot + urgent ntfy + exit 2.
- **Verify-then-swap** — every write is confirmed by re-parsing the roster; failures reset the UI state.
- **`PAUSED` file kills writes instantly** — the run still reports what it *would* have done (`[PAUSED] planned: …`).
- **`min_swap_gain` threshold** — no churn swaps for marginal projection edges.

---

## 2. Step-by-step implementation

Work through these in order; each step ends with an acceptance check. Steps 1–2 are Phase 0, steps 3–5 Phase 2, step 6 Phase 3, steps 7–8 Phase 4, step 9 Phase 5 of the original build plan.

### Step 1 — Clone and install (lid open)

```bash
git clone https://github.com/BEXAI/YahooFantasyFootball ~/ffl-agent
cd ~/ffl-agent
./install.sh        # venv + Playwright Chromium + creates config.json from template
```

**Accept:** `install.sh` completes and prints `playwright import: ok`.

### Step 2 — Configure

1. Edit `config.json`: `league_id` + `team_id` (from your team URL), a random `ntfy_topic`, and your `healthchecks_url`. Adjust `slot_eligibility` if your league's slots differ (§1.3).
2. **Power hygiene** (System Settings, one-time): turn **OFF** "Install macOS updates" under Automatic Updates (an overnight auto-restart clears `disablesleep` and logs you out, killing LaunchAgents until next login; leave security responses on). Keep the Mac on AC on a hard, ventilated surface. "Optimized Charging" can stay on.
3. **Notifications:** install the ntfy app on the iPhone and subscribe to your topic; at healthchecks.io create one check with 2 schedules (Thu 17:30 and Sun 10:30 America/New_York, grace 30m) and paste its ping URL into `config.json`.

### Step 3 — Seed the Yahoo session (lid open, once)

```bash
cd ~/ffl-agent
.venv/bin/python seed_login.py
```

A headed Chromium opens: log in fully (incl. 2FA), tick **"Stay signed in"**, confirm your roster renders, then press Enter in the terminal.
**Accept:** `screenshots/seed_verify.png` shows your logged-in roster.

### Step 4 — First dry run (headless, exactly how launchd will run it)

```bash
DRY_RUN=1 .venv/bin/python set_lineup.py
cat logs/last_status.json
open screenshots/       # before_*.png must show your roster, not a sign-in page
```

**Accept:** exit 0, status `NO_CHANGE` with a `[DRY] planned:` summary (sensible swaps or truly none), no `LOGIN_REQUIRED`.

### Step 5 — Pin the DOM contract (required before any live write)

Open `before_*.png` plus the live roster page's HTML and pin the three `Phase 2: pin` markers in `set_lineup.py` to exact selectors: (1) projected-points column index in `parse_roster`, (2) slot-control locator in `click_slot_control`, (3) lock detection in `parse_roster`. Re-run step 4.
**Accept:** planned swaps in `last_status.json` are sane against the real roster (projections match what the page shows).

### Step 6 — One supervised LIVE run (lid open)

```bash
.venv/bin/python set_lineup.py          # LIVE — watch it
cat logs/last_status.json
open screenshots/                        # after_*.png must match the reported swaps
```

**Accept:** `SUCCESS`/`NO_CHANGE`; ntfy push received on the iPhone; healthchecks shows a ping; the Yahoo iOS app reflects the change.

### Step 7 — Arm lid-closed operation

```bash
./arm.sh    # sudo pmset -a disablesleep 1 + installs Thu/Sun LaunchAgents
```

`arm.sh` rewrites the plists to this clone's actual path, so the armed schedules run exactly what you just validated.
**Accept:** `pmset -g` prints `SleepDisabled 1`; `launchctl list | grep com.nathaniel.ffl` shows both jobs.

### Step 8 — Lid-closed smoke test

```bash
./smoke_test.sh     # schedules a DRY run 3 minutes out
# plug into AC, CLOSE THE LID, wait 5 minutes, then reopen:
ls -lt logs | head -3               # a run_*.log timestamped while the lid was closed
cat logs/last_status.json           # DRY status generated lid-closed = PASS
./smoke_test.sh --cleanup
```

**Accept:** the smoke run executed and pinged healthchecks **while the lid was closed**. From here on: leave the Mac on AC, lid closed, logged in. You're live.

### Step 9 — Hardening drills (first quiet week)

1. **Forced-failure drill:** `mv profile profile.bak && .venv/bin/python set_lineup.py` → expect `LOGIN_REQUIRED`, urgent ntfy, exit 2, zero writes; then `mv profile.bak profile`.
2. **Dead-man drill:** `launchctl unload ~/Library/LaunchAgents/com.nathaniel.ffl.thu.plist` for one week → confirm healthchecks alerts "check is down"; reload after.
3. **Pause gate:** `touch ~/ffl-agent/PAUSED` before any week you want manual control; `rm ~/ffl-agent/PAUSED` to resume.
4. **Optional Claude handoff:** before any run, have Claude (chat/mobile) output swaps and save from your phone via SSH: `echo '{"swaps":[{"out":"Player A","in":"Player B"}]}' > ~/ffl-agent/lineup.json` — fresh file overrides the optimizer (§1.3).
5. **iPhone console:** Termius profile → `cd ~/ffl-agent && ./run.sh` for on-demand re-runs; ntfy app for alerts.

### Step 10 — Enable the research layer (optional, recommended)

The two Routines (Thu 20:00 / Sun 13:00 UTC) and all executor plumbing ship with the repo — enabling real advice is data entry:

1. Edit `roster.json` on `main`: replace every `Placeholder` entry with your actual roster, names **exactly** as Yahoo renders them. (Until then, Routines run harmless SMOKE MODE pushes.)
2. Edit `league_settings.json` to match your league's slots, eligibility, and scoring — keep `slot_eligibility` identical to your Mac's `config.json`.
3. On the Mac: `git pull` so `run.sh` gains the advice-fetch step, then `DRY_RUN=1 ./run.sh` — the fetch/extract step lives in `run.sh` (calling `set_lineup.py` directly skips it), and `last_status.json` should show `source: advice` when fresh advice exists.
4. Decide repo visibility (§1.5b note): private hides your planned lineup from league-mates but requires a read credential on the Mac.
5. Update `roster.json` after every add/drop — advice naming dropped players simply fails validation and the optimizer covers the gap.

**Accept:** a Routine firing produces an advice commit on `main`, a push notification on the iPhone, and the next Mac dry run reports `source: advice`.

---

## 3. Reference

### 3.1 Failure modes

| Failure | Detection | Response |
|---|---|---|
| Yahoo session expired / challenge | Login-guard → `LOGIN_REQUIRED`, urgent ntfy, exit 2, zero writes | Lid open, `python seed_login.py`, re-seed (residential IP = low recurrence) |
| macOS update reset `disablesleep` / reboot logged out | `run.sh` sentinel warns; missed run → healthchecks dead-man alert | `sudo pmset -a disablesleep 1`; log in; keep auto-install OFF |
| Player locked (game started) | Click timeout / verify fail → skipped entries in `PARTIAL` | Expected near locks; earlier run windows already buffer this |
| Yahoo DOM redesign | Parse returns 0 players → `ERROR` exit 4; or verify-fail `PARTIAL` | Re-pin the 3 DOM-contract selectors (step 5 procedure) |
| Power outage → battery drained → Mac off | Missed ping → healthchecks alert | Manual power-on + login (Apple Silicon can't auto-boot); battery rides out short outages |
| Critical battery force-sleep | Same dead-man alert | Confirm AC connection/adapter |
| `lineup.json` stale or malformed | Ignored (freshness / shape check) → advice or optimizer ran | Fix or delete the file if you wanted the override |
| `lineup.json` fresh but names typo'd | Unmatched entries skipped (after name normalization) → `NO_CHANGE`, `source: manual`. A fresh manual override is authoritative: the optimizer does **not** run, so it can't churn a lineup you set deliberately | Fix the names (or delete the file to re-enable the optimizer) |
| Routine didn't fire / push failed / advice stale or future-dated | `generated_at` freshness check discards it (stale, or >1h in the future) → optimizer ran (`source: optimizer`) | Nothing urgent — check the operator session's Routine firings if it repeats |
| Routine advice names nobody on the roster | Zero entries validate → optimizer ran | Update `roster.json` (names must match Yahoo's rendering) |
| Player ruled OUT after advice was generated | Executor's own `startable()` check (live Yahoo badge) rejects that entry at run time | None — guardrail did its job |

### 3.2 Ops notes & revert

- **Post-macOS-update checklist (2 min):** log in → `pmset -g | grep SleepDisabled` (re-arm if 0) → `launchctl list | grep ffl` → `DRY_RUN=1 .venv/bin/python set_lineup.py`.
- **Travel with this MacBook:** `StartCalendarInterval` uses the Mac's local clock — in another timezone your 17:30 fires at *local* 17:30, and the home network/IP advantage is gone. Either leave the Mac home on AC, or `touch PAUSED` and run manually via SSH.
- **Known schedule edge:** on rare weeks with a 9:30 AM ET international game, those players are already locked before the Sun 10:30 run; the agent skips them safely and reports. Move the Sunday plist earlier if your roster is exposed to those games.
- **Season teardown / full revert:** `./teardown.sh` (re-enables sleep, removes all launchd schedules).

### 3.3 Triggers to leave this path

- **>1 missed run per month** from sleep-flag resets/reboots → move the executor to a **Raspberry Pi 5 / used Mac mini**. The entire folder ports as-is (swap launchd plists for systemd timers; use `chromium`, not branded Chrome, on ARM64 Linux).
- **You need the MacBook with you** on game days → same migration; this design's only hard requirement is *this* machine at home on AC.
- **>1 `LOGIN_REQUIRED` per month** → cookie longevity issue: re-seed and confirm "Stay signed in"; a persistent residential setup should hold for weeks.
- **Yahoo ships a write scope (`fspt-w`)** → retire the browser write path entirely for an API call.
- **Thermals** (Mac hot to touch under closed lid) → prop the lid slightly with an external display attached, or migrate to the Pi.

**Bottom line:** this is the correct zero-hardware-cost implementation of "MacBook closed" — deterministic launchd timing, residential IP, persistent profile, no Claude/auth dependencies at runtime — accepted as a season-long stopgap with the Pi migration pre-planned as the durable endgame.

### 3.4 Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/pytest tests/ -v      # decision-logic unit tests, no browser needed
```

CI (`.github/workflows/tests.yml`) runs the test suite and shell syntax checks on every push.
