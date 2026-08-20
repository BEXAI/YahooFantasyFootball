#!/bin/bash
# Phase 4 (7.1–7.2) — arm lid-closed operation: disable sleep + install schedules.
# Requires admin password for pmset. Run AFTER Phase 2 (seed login + dry run)
# and Phase 3 (supervised live run) have passed.
set -euo pipefail
AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$AGENT_DIR"

echo "== 7.1 Keep the Mac awake with the lid closed =="
sudo pmset -a disablesleep 1
pmset -g | grep SleepDisabled          # MUST print: SleepDisabled 1

echo "== 7.2 Install the schedules =="
# The committed plists reference ~/ffl-agent; rewrite to wherever this clone
# actually lives so the armed schedules run the exact path the smoke test ran.
mkdir -p "$HOME/Library/LaunchAgents"
for job in thu sun; do
  PLIST="$HOME/Library/LaunchAgents/com.nathaniel.ffl.$job.plist"
  sed "s|\$HOME/ffl-agent|'$AGENT_DIR'|" "launchd/com.nathaniel.ffl.$job.plist" > "$PLIST"
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
done
launchctl list | grep com.nathaniel.ffl

echo "Armed. Next: ./smoke_test.sh for the lid-closed smoke test (7.3)."
