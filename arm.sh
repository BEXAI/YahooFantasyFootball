#!/bin/bash
# Phase 4 (7.1–7.2) — arm lid-closed operation: disable sleep + install schedules.
# Requires admin password for pmset. Run AFTER Phase 2 (seed login + dry run)
# and Phase 3 (supervised live run) have passed.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== 7.1 Keep the Mac awake with the lid closed =="
sudo pmset -a disablesleep 1
pmset -g | grep SleepDisabled          # MUST print: SleepDisabled 1

echo "== 7.2 Install the schedules =="
cp launchd/com.nathaniel.ffl.thu.plist launchd/com.nathaniel.ffl.sun.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.nathaniel.ffl.thu.plist
launchctl load ~/Library/LaunchAgents/com.nathaniel.ffl.sun.plist
launchctl list | grep com.nathaniel.ffl

echo "Armed. Next: ./smoke_test.sh for the lid-closed smoke test (7.3)."
