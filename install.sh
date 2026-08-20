#!/bin/bash
# Phase 0 — toolchain + Python env + Playwright Chromium (run on the MacBook).
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== Toolchain check =="
python3 --version                      # want 3.10+
xcode-select -p >/dev/null 2>&1 || xcode-select --install

echo "== Python env + Playwright Chromium (headless-capable build) =="
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium

mkdir -p logs screenshots

if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo ">>> Created config.json from template — EDIT IT NOW:"
  echo ">>>   league_id / team_id  (from https://football.fantasysports.yahoo.com/f1/<league_id>/<team_id>)"
  echo ">>>   ntfy_topic           (random topic; subscribe in the iPhone ntfy app)"
  echo ">>>   healthchecks_url     (create a check at healthchecks.io, paste its ping URL)"
fi

.venv/bin/python -c "import playwright; print('playwright import: ok')"
echo "Phase 0 complete."
