# FFL Agent — Yahoo Fantasy Football Auto-Lineup ("A2: Lid CLOSED" path)

Sets your Yahoo Fantasy Football lineup automatically before Thursday and Sunday locks, from an Apple Silicon MacBook that stays **lid-closed on AC power** all season. Pure Playwright (no Claude at runtime) · headless Chromium · launchd. The iPhone is the monitor/recovery console (ntfy alerts + SSH); it has no execution role.

---

## 1. System walkthrough

### 1.1 Big picture

```
MacBook (Apple Silicon · AC power · lid CLOSED · pmset disablesleep=1)
│
├─ launchd LaunchAgents (Mac local time = ET in Miami)
│    ├─ com.nathaniel.ffl.thu   → Thu 17:30  (≈2h45m before TNF lock)
│    └─ com.nathaniel.ffl.sun   → Sun 10:30  (≈2h30m before early locks)
│         │
│         └─ run.sh
│              ├─ sentinel: SleepDisabled==1 ? (warn via ntfy if not)
│              ├─ caffeinate -i  .venv/bin/python set_lineup.py
│              │     ├─ 0 PAUSED file present ? → report planned swaps
│              │     │            only ([PAUSED]), zero writes, exit 0
│              │     ├─ 1 READ    headless persistent Chromium (./profile)
│              │     │            login-guard → abort+notify if sign-in page
│              │     │            parse roster: slot, player, status, bye,
│              │     │            Yahoo projected pts, locked flag
│              │     ├─ 2 DECIDE  lineup.json override if fresh, else
│              │     │            greedy optimizer (eligibility map,
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
| `run.sh` | Entrypoint: reads config, warns via ntfy if `SleepDisabled` got reset, runs the agent under `caffeinate -i`, pings healthchecks (`…/fail` on nonzero exit), rotates logs (keep 30) and screenshots (keep 40). |
| `set_lineup.py` | The agent — the five stages in the diagram. Everything below (§1.3–§1.6) is its contract. |
| `seed_login.py` | One-time, lid-open, **headed** login that seeds `./profile` (persistent Chromium cookies). The agent never touches credentials — it only reuses this profile. |
| `install.sh` / `arm.sh` / `smoke_test.sh` / `teardown.sh` | Setup, arming, lid-closed smoke test, and full revert — used in §2. |
| `tests/` | 32 browser-free unit tests over the decision logic; run by CI (`.github/workflows/tests.yml`). |

### 1.3 Data files and their schemas

**`config.json`** (copy of `config.example.json`, gitignored — your real ids/URLs never get committed):

| Field | Meaning |
|---|---|
| `league_id`, `team_id` | From your team URL: `https://football.fantasysports.yahoo.com/f1/<league_id>/<team_id>` |
| `min_swap_gain` | Bench must project at least this many points above a healthy starter to justify a swap (anti-churn threshold; default `1.0`) |
| `lineup_json_max_age_hours` | Override freshness window (default `20`) — older `lineup.json` is ignored |
| `ntfy_topic` | Random topic name; subscribe to it in the iPhone ntfy app |
| `healthchecks_url` | Ping URL of your healthchecks.io check |
| `slot_eligibility` | Map of **primary position → slots that position may fill**, e.g. `"RB": ["RB", "W/R/T"]`. Adjust for your league's flex types (superflex: add `"Q/W/R/T"` entries) |
| `bad_statuses` | Statuses that are never startable: `["O","IR","SUSP","NA","PUP","NFI"]` (`Q` and `D` remain startable) |

**`lineup.json`** (optional override, gitignored; format in `lineup.sample.json`):

```json
{"swaps": [{"out": "Starter Player Name", "in": "Bench Player Name"}]}
```

Fresh (< `lineup_json_max_age_hours`) → it is **authoritative**: their swaps are validated (names must match, bench player startable/unlocked/eligible, no player twice) and the optimizer does not run. Stale, malformed, or wrong-shaped → ignored, optimizer runs.

**`logs/last_status.json`** (written by every run):

```json
{"status": "SUCCESS", "ts": "20260910T173012", "dry_run": false,
 "summary": "RB: Player A → Player B (+3.5)", "source": "optimizer"}
```

`status` ∈ `SUCCESS` · `NO_CHANGE` · `PARTIAL` · `LOGIN_REQUIRED` · `ERROR`. The same summary goes out as the ntfy push. Exit codes: `0` ok/no-change/partial-with-progress, `2` login required, `3` write error (no swaps applied), `4` parse error.

**Other state:** `profile/` (persistent Chromium cookies — never commit), `screenshots/before_*.png` / `after_*.png` (evidence per run), `PAUSED` (touch to disable writes instantly, see §1.6).

### 1.4 The DECIDE stage

1. If a **fresh `lineup.json`** exists, use it (validated, authoritative — see §1.3).
2. Otherwise the **greedy optimizer**, two conservative passes:
   - **Pass 1 — never start a dead slot:** every unstartable starter (BYE or bad status) is replaced by the highest-projected startable, unlocked, slot-eligible bench player.
   - **Pass 2 — upgrades over the threshold:** a healthy, unlocked starter is upgraded only when a bench candidate projects ≥ `min_swap_gain` points higher.
   - Each bench player is used at most once; `Q`/`D` players are startable; locked bench players are never swapped in.

This is deliberately not a global optimum search — it is a conservative, explainable pass that never leaves a BYE/OUT player in and never churns for marginal gains.

### 1.5 The WRITE + VERIFY stages

Writes use Yahoo's **Swap Mode**: click the starter's slot control, then click the highlighted bench player. After every swap the roster is **re-parsed and the swap confirmed** before continuing; after any failure (timeout, verify miss) the page is hard-reloaded so a dangling selection can never turn the next click into an unplanned write, and each swap is preceded by a pre-check that the outgoing starter still holds the planned slot. Before/after full-page PNGs are kept for every run.

> **DOM contract:** three selectors are deliberately loose and tagged `Phase 2: pin` in `set_lineup.py` — the projected-points column index, the slot-control locator, and lock detection. They MUST be pinned against your live roster page (step 5 in §2) before any live write. Everything else is behavior, not guesswork.

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
git clone https://github.com/BEXAI/YahooFantassyFootball ~/ffl-agent
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
| `lineup.json` stale or malformed | Ignored (freshness / shape check) → optimizer fallback ran | Fix or delete the file if you wanted the override |
| `lineup.json` fresh but names typo'd | Unmatched entries skipped → `NO_CHANGE (lineup.json)`. A fresh override is authoritative: the optimizer does **not** run, so it can't churn a lineup you set deliberately | Fix the names (or delete the file to re-enable the optimizer) |

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
