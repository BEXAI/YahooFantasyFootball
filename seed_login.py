#!/usr/bin/env python3
"""ONE-TIME: headed login to seed the persistent profile. Run with lid OPEN."""
import json, pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent
cfg_path = ROOT / "config.json"
if not cfg_path.exists():
    raise SystemExit("config.json not found — copy config.example.json to config.json first.")
cfg = json.loads(cfg_path.read_text())
url = f"https://football.fantasysports.yahoo.com/f1/{cfg['league_id']}/{cfg['team_id']}"

(ROOT / "screenshots").mkdir(exist_ok=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        str(ROOT / "profile"), headless=False, viewport={"width": 1366, "height": 900}
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(url, wait_until="domcontentloaded")
    print("\n>>> Log in fully in the Chromium window (incl. 2FA).")
    print(">>> Check 'Stay signed in'. Confirm your ROSTER renders, then return here.")
    input(">>> Press Enter to save the session and exit... ")
    page.goto(url, wait_until="domcontentloaded")
    page.screenshot(path=str(ROOT / "screenshots" / "seed_verify.png"), full_page=True)
    ctx.close()
print("Profile seeded → ./profile  (verify screenshots/seed_verify.png shows your roster)")
