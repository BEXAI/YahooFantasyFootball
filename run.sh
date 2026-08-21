#!/bin/bash
# launchd entrypoint: sentinel + caffeinate + healthchecks dead-man ping.
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TS=$(date +%Y%m%dT%H%M%S)
mkdir -p logs screenshots
LOG="logs/run_$TS.log"
# one python process for both values (was two identical parses of config.json)
{ read -r HC; read -r TOPIC; } < <(python3 -c \
  'import json;c=json.load(open("config.json"));print(c["healthchecks_url"]);print(c["ntfy_topic"])') || { HC=""; TOPIC=""; }

# Research-layer advice: extract the latest committed advice WITHOUT merging
# (git show never touches the working tree state). Offline => keep the previous
# extract; its embedded generated_at freshness check governs. If fetch works but
# the file is gone from main, drop the stale local copy.
# GIT_TERMINAL_PROMPT=0: under launchd there is no TTY — a private repo with a
# missing credential must fail fast here, never sit on a prompt into lock time.
if GIT_TERMINAL_PROMPT=0 git fetch origin main >/dev/null 2>&1; then
  git show FETCH_HEAD:advice/lineup.json > advice_remote.json 2>/dev/null \
    || rm -f advice_remote.json
fi

# Sentinel: if the sleep flag got reset (macOS update/reboot), this run only
# happened because the lid is open — warn so you re-arm before the next one.
if ! pmset -g | grep "SleepDisabled" | grep -q "1"; then
  curl -s -H "Title: FFL Agent" -H "Priority: high" \
       -d "WARNING: SleepDisabled=0 — re-run: sudo pmset -a disablesleep 1" \
       "https://ntfy.sh/$TOPIC" >/dev/null || true
fi

caffeinate -i .venv/bin/python set_lineup.py >> "$LOG" 2>&1
CODE=$?

# Dead-man ping: /fail suffix on nonzero exit
if [ $CODE -eq 0 ]; then curl -fsS -m 10 "$HC" >/dev/null || true
else curl -fsS -m 10 "$HC/fail" >/dev/null || true; fi

# Keep last 30 logs / 40 screenshots / 40 api evidence files
ls -1t logs/run_*.log 2>/dev/null | tail -n +31 | xargs -I{} rm -f {}
ls -1t screenshots/*.png 2>/dev/null | tail -n +41 | xargs -I{} rm -f {}
ls -1t logs/api_roster_*.json 2>/dev/null | tail -n +41 | xargs -I{} rm -f {}
exit $CODE
