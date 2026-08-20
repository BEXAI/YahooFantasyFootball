#!/bin/bash
# Season teardown / full revert (§10): restore sleep, remove schedules.
set -uo pipefail
sudo pmset -a disablesleep 0
launchctl unload ~/Library/LaunchAgents/com.nathaniel.ffl.thu.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.nathaniel.ffl.sun.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.nathaniel.ffl.smoke.plist 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.nathaniel.ffl.*.plist
echo "Reverted: sleep re-enabled, launchd schedules removed."
echo "The project folder, profile, and logs are untouched — delete manually if desired."
