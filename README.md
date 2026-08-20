# FFL Agent — Yahoo Fantasy Football Auto-Lineup ("A2: Lid CLOSED" path)

Pure Playwright (no Claude at runtime) · headless Chromium · launchd · MacBook lid closed on AC power.

Sets your Yahoo Fantasy Football lineup automatically before Thursday and Sunday locks, from an Apple Silicon MacBook that stays **lid-closed on AC power** all season. The iPhone is the monitor/recovery console (ntfy alerts + SSH); it has no execution role.

- **Host:** Apple Silicon MacBook, macOS, Miami/ET local time, plugged into AC, lid closed 24/7 during season.
- **Runtime dependencies:** Python + Playwright Chromium only. Zero Claude, zero Anthropic auth, zero MCP at runtime. Claude is optional at decide-time via the `lineup.json` handoff.

## Verdict logic (read first)

| Question | Answer |
|---|---|
| Does this satisfy "MacBook closed"? | **Yes** — lid closed, on AC, `disablesleep=1`. It does **not** satisfy "MacBook off" (physically impossible on Apple Silicon; no scheduled cold boot). |
| Why does it work? | `sudo pmset -a disablesleep 1` sets a kernel flag that vetoes sleep even lid-closed with no display. The Mac stays awake, so launchd fires on time. **Headless** Chromium needs no display, so the browser runs fine. Headed would fail (no display exists lid-closed). |
| Why is it fragile? | Undocumented flag; macOS updates/reboots can reset it; forced sleep at critical battery; heat under a closed lid; after any reboot, user LaunchAgents don't run until you log in (FileVault). Mitigated by a pre-run sentinel + healthchecks.io dead-man alerts. |
| When to abandon this path | See [Triggers to leave this path](#triggers-to-leave-this-path). |

**Hard guardrails (encoded in `set_lineup.py`):** position swaps only — never add/drop/trade; never start BYE/OUT/IR/SUSP; skip locked players and report; abort with zero writes on any login/sign-in page; idempotent verify-then-swap; `PAUSED` file kills writes instantly; min-gain threshold prevents churn swaps.

## Architecture

```
MacBook (Apple Silicon · AC power · lid CLOSED · pmset disablesleep=1)
│
├─ launchd LaunchAgents (Mac local time = ET in Miami)
│    ├─ com.nathaniel.ffl.thu   → Thu 17:30  (≈2h45m before TNF lock)
│    └─ com.nathaniel.ffl.sun   → Sun 10:30  (≈2h30m before early locks)
│         │
│         └─ run.sh
│              ├─ sentinel: SleepDisabled==1 ? (warn via ntfy if not)
│              ├─ PAUSED file present ? → exit 0, no writes
│              ├─ caffeinate -i  .venv/bin/python set_lineup.py
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

## Repository layout

| Path | Role |
|---|---|
| `config.example.json` | Template → copy to `config.json` (gitignored) and fill in ids/URLs |
| `seed_login.py` | ONE-TIME headed Yahoo login (lid OPEN) to seed the browser profile |
| `set_lineup.py` | Main agent: read → decide → write → verify → notify |
| `run.sh` | launchd entrypoint: sentinel + caffeinate + healthchecks ping |
| `install.sh` | Phase 0: venv + Playwright Chromium + config bootstrap |
| `arm.sh` | Phase 4: `pmset disablesleep` + install launchd schedules |
| `smoke_test.sh` | Phase 4: lid-closed smoke test (temp DRY job 3 min out) |
| `teardown.sh` | Season teardown / full revert |
| `launchd/*.plist` | Thu 17:30 & Sun 10:30 LaunchAgents |
| `lineup.sample.json` | Format for the optional Claude-computed override |
| `tests/` | Unit tests for the decision logic (no browser needed) |
| `profile/` | Persistent Chromium profile (cookies) — **never commit** (gitignored) |
| `logs/`, `screenshots/` | Run logs, `last_status.json`, before/after PNGs (gitignored) |
| `PAUSED` | Touch this file to disable writes instantly (absent by default) |

> The launchd plists assume the project lives at `~/ffl-agent`. Clone accordingly:
> `git clone https://github.com/BEXAI/YahooFantassyFootball ~/ffl-agent`
> (or edit the two plists if you keep it elsewhere).

## Phase 0 — Prerequisites & power hygiene

```bash
git clone https://github.com/BEXAI/YahooFantassyFootball ~/ffl-agent
cd ~/ffl-agent
./install.sh
# then EDIT config.json (league_id, team_id, ntfy_topic, healthchecks_url)
```

Find `league_id`/`team_id` in your team URL: `https://football.fantasysports.yahoo.com/f1/<league_id>/<team_id>`.
`slot_eligibility` maps a player's **primary position → slots he may fill**; adjust if your league uses different flex types (e.g., add `"Q/W/R/T"` superflex entries).

**Power hygiene (one-time, in System Settings):**
1. **General → Software Update → Automatic Updates:** turn OFF "Install macOS updates" (an overnight auto-restart clears `disablesleep` AND logs you out, killing LaunchAgents until next login). Leave security responses on; install updates manually on your schedule.
2. Keep the MacBook **on AC power** on a hard, ventilated surface (closed lid traps heat; one headless page is light, but don't stack it on a blanket).
3. Battery "Optimized Charging" can stay on.

**Acceptance:** `install.sh` completes and prints `playwright import: ok`.

## Phase 2 — Seed login + validated DRY RUN (lid OPEN)

```bash
cd ~/ffl-agent
# One-time login (headed, lid open). Complete 2FA; tick "Stay signed in".
.venv/bin/python seed_login.py

# Prove the session persists HEADLESS (exactly how launchd will run it)
DRY_RUN=1 .venv/bin/python set_lineup.py
cat logs/last_status.json
open screenshots/    # before_*.png must show your logged-in roster (no sign-in page)
```

**DOM contract — MUST be pinned before any live write.** Three markers in `set_lineup.py` are deliberately loose and tagged `Phase 2: pin`:
1. the projected-points column index in `parse_roster` (currently "first numeric cell"),
2. the slot-control locator in `click_slot_control`,
3. lock detection in `parse_roster`.

Open `before_*.png` plus the live page HTML, pin all three to exact selectors, re-run `DRY_RUN=1`, and confirm the planned swaps in `last_status.json` are sane against the real roster. Everything else is behavior, not guesswork.

**Acceptance:** dry run exits 0; status is `NO_CHANGE` with a `[DRY] planned:` summary listing sensible swaps (or truly no swaps); no `LOGIN_REQUIRED`.

## Phase 3 — Supervised LIVE write (lid OPEN, once)

Set up notifications first: install the **ntfy** app on iPhone and subscribe to your topic from `config.json`; create a **healthchecks.io** check (2 schedules: Thu 17:30, Sun 10:30 America/New_York, grace 30m) and paste its ping URL into `config.json`.

```bash
cd ~/ffl-agent
.venv/bin/python set_lineup.py          # LIVE — watch it
cat logs/last_status.json
open screenshots/                        # after_*.png must match the reported swaps
```

Then verify in the Yahoo iOS app that the lineup actually changed.
**Acceptance:** `SUCCESS`/`NO_CHANGE`; ntfy push received on iPhone; healthchecks shows a ping; Yahoo app reflects the swap.

## Phase 4 — Arm lid-closed operation

```bash
cd ~/ffl-agent
./arm.sh            # sudo pmset -a disablesleep 1 + load Thu/Sun LaunchAgents
./smoke_test.sh     # schedules a DRY run 3 min out — close the lid, wait 5 min
```

After 5 minutes, open the lid:

```bash
ls -lt logs | head -3                # a run_*.log timestamped while the lid was closed
cat logs/last_status.json            # DRY status generated lid-closed = PASS
./smoke_test.sh --cleanup
```

**Acceptance:** the smoke run executed and pinged healthchecks **while the lid was closed**. From here on: leave the Mac on AC, lid closed, logged in. You're live.

## Phase 5 — Hardening

1. **Forced-failure drill:** `mv profile profile.bak && .venv/bin/python set_lineup.py` → expect `LOGIN_REQUIRED`, urgent ntfy, exit 2, zero writes; then `mv profile.bak profile`.
2. **Dead-man drill:** disable the Thursday plist for one week (`launchctl unload ~/Library/LaunchAgents/com.nathaniel.ffl.thu.plist`) and confirm healthchecks emails/pushes a "check is down" alert; reload after.
3. **Pause gate:** `touch ~/ffl-agent/PAUSED` before any week you want manual control (playoffs, weird matchups); `rm ~/ffl-agent/PAUSED` to resume.
4. **Optional Claude handoff:** any time before a run, have Claude (chat/mobile — MacBook irrelevant) output swaps and save from your phone via SSH:
   `echo '{"swaps":[{"out":"Player A","in":"Player B"}]}' > ~/ffl-agent/lineup.json`
   Fresh file (< 20h, per `lineup_json_max_age_hours`) overrides the optimizer; stale is ignored. See `lineup.sample.json`.
5. **iPhone console:** Termius profile → `cd ~/ffl-agent && ./run.sh` for on-demand re-runs; ntfy app for alerts.

## Failure modes

| Failure | Detection | Response |
|---|---|---|
| Yahoo session expired / challenge | Login-guard → `LOGIN_REQUIRED`, urgent ntfy, exit 2, zero writes | Lid open, `python seed_login.py`, re-seed (residential IP = low recurrence) |
| macOS update reset `disablesleep` / reboot logged out | `run.sh` sentinel warns; missed run → healthchecks dead-man alert | `sudo pmset -a disablesleep 1`; log in; keep auto-install OFF |
| Player locked (game started) | Click timeout / verify fail → skipped entries in `PARTIAL` | Expected near locks; earlier run windows already buffer this |
| Yahoo DOM redesign | Parse returns 0 players → `ERROR` exit 4; or verify-fail `PARTIAL` | Re-pin the 3 DOM-contract selectors (Phase 2 procedure) |
| Power outage → battery drained → Mac off | Missed ping → healthchecks alert | Manual power-on + login (Apple Silicon can't auto-boot); battery rides out short outages |
| Critical battery force-sleep | Same dead-man alert | Confirm AC connection/adapter |
| `lineup.json` stale/typo'd names | Ignored by freshness check / unmatched names skipped | Optimizer fallback ran; fix file if intended |

## Ops notes & revert

- **Post-macOS-update checklist (2 min):** log in → `pmset -g | grep SleepDisabled` (re-arm if 0) → `launchctl list | grep ffl` → `DRY_RUN=1 .venv/bin/python set_lineup.py`.
- **Travel with this MacBook:** `StartCalendarInterval` uses the Mac's local clock — in another timezone your 17:30 fires at *local* 17:30, and the home network/IP advantage is gone. Either leave the Mac home on AC, or `touch PAUSED` and run manually via SSH.
- **Season teardown / full revert:** `./teardown.sh` (re-enables sleep, removes all launchd schedules).

## Triggers to leave this path

- **>1 missed run per month** from sleep-flag resets/reboots → move the executor to a **Raspberry Pi 5 / used Mac mini**. The entire folder ports as-is (swap launchd plists for systemd timers; use `chromium`, not branded Chrome, on ARM64 Linux).
- **You need the MacBook with you** on game days → same migration; this design's only hard requirement is *this* machine at home on AC.
- **>1 `LOGIN_REQUIRED` per month** → cookie longevity issue: re-seed and confirm "Stay signed in"; a persistent residential setup should hold for weeks.
- **Yahoo ships a write scope (`fspt-w`)** → retire the browser write path entirely for an API call.
- **Thermals** (Mac hot to touch under closed lid) → prop the lid slightly with an external display attached, or migrate to the Pi.

**Bottom line:** this is the correct zero-hardware-cost implementation of "MacBook closed" — deterministic launchd timing, residential IP, persistent profile, no Claude/auth dependencies at runtime — accepted as a season-long stopgap with the Pi migration pre-planned as the durable endgame.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/pytest tests/ -v      # decision-logic unit tests, no browser needed
```

Exit codes of `set_lineup.py`: `0` ok/no-change/partial-with-progress, `2` `LOGIN_REQUIRED`, `3` write error (no swaps applied), `4` parse error.
