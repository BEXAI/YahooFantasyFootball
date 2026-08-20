#!/bin/bash
# launchd entrypoint: sentinel + caffeinate + healthchecks dead-man ping.
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TS=$(date +%Y%m%dT%H%M%S)
mkdir -p logs screenshots
LOG="logs/run_$TS.log"
HC=$(python3 -c 'import json;print(json.load(open("config.json"))["healthchecks_url"])')
TOPIC=$(python3 -c 'import json;print(json.load(open("config.json"))["ntfy_topic"])')

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

# Keep last 30 logs / 40 screenshots
ls -1t logs/run_*.log 2>/dev/null | tail -n +31 | xargs -I{} rm -f {}
ls -1t screenshots/*.png 2>/dev/null | tail -n +41 | xargs -I{} rm -f {}
exit $CODE
