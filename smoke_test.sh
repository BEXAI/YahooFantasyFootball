#!/bin/bash
# Phase 4 (7.3) — LID-CLOSED SMOKE TEST: schedule a DRY run 3 minutes out,
# then close the lid and wait 5 minutes. Verify with: cat logs/last_status.json
# Usage: ./smoke_test.sh            schedule the smoke job
#        ./smoke_test.sh --cleanup  remove the temp smoke job afterwards
set -euo pipefail
AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.nathaniel.ffl.smoke.plist"

if [ "${1:-}" = "--cleanup" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Smoke job removed."
  exit 0
fi

# single date call: two separate calls could straddle a minute/hour boundary
# and schedule the job an hour off (H from one time, M from another)
read -r H M < <(date -v+3M "+%H %M")

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.nathaniel.ffl.smoke</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd '$AGENT_DIR' &amp;&amp; DRY_RUN=1 ./run.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$H</integer><key>Minute</key><integer>$M</integer></dict>
</dict></plist>
PLIST

launchctl unload "$PLIST" 2>/dev/null || true    # re-run: drop any stale fire time
launchctl load "$PLIST"
echo "Smoke job scheduled for $H:$M. Now: plug into AC, CLOSE THE LID, wait 5 minutes."
echo "Then open the lid and run:"
echo "  ls -lt logs | head -3          # a run_*.log timestamped while the lid was closed"
echo "  cat logs/last_status.json      # DRY status generated lid-closed = PASS"
echo "  ./smoke_test.sh --cleanup      # remove the temp smoke job"
