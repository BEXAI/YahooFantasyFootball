#!/usr/bin/env python3
"""ONE-TIME (and re-run on AuthExpired): Yahoo OAuth2 bootstrap — plan P0.T2.

Creates ~/.ffl-secrets/yahoo.json (chmod 600). Prerequisite (P0.T1, human):
a Yahoo developer app at https://developer.yahoo.com/apps/create/ with
API Permissions -> Fantasy Sports -> Read/Write, redirect URI
https://localhost:8080 (the browser will fail to load that page — that's
expected; you copy the ?code= value out of the address bar).

Stdlib only. Never commits or prints full tokens.
"""
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from yahoo_api import AUTH_URL, TOKEN_URL, DEFAULT_SECRETS_PATH  # noqa: E402

REDIRECT_URI = "https://localhost:8080"


def main():
    path = DEFAULT_SECRETS_PATH
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text())
        print(f"Updating existing {path}")
    client_id = existing.get("client_id") or input("Yahoo app Client ID (Consumer Key): ").strip()
    client_secret = existing.get("client_secret") or input("Yahoo app Client Secret: ").strip()

    q = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT_URI,
        "response_type": "code", "language": "en-us"})
    print("\n1) Open this URL, sign in to the Yahoo account that owns the team,")
    print("   and click Agree:\n")
    print(f"   {AUTH_URL}?{q}\n")
    print("2) The browser will land on an unreachable https://localhost:8080/?code=...")
    print("   page — copy the value of the code parameter from the address bar.")
    code = input("\nPaste the code: ").strip()

    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI, "code": code,
        "grant_type": "authorization_code"}).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.loads(r.read().decode())

    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**existing,
            "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "access_token": tok["access_token"],
            "refresh_token": tok["refresh_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 3600))}
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)
    print(f"\nSaved {path} (chmod 600).")
    print(f"refresh_token ends with ...{tok['refresh_token'][-6:]} — never commit this file.")
    print("Next: .venv/bin/python scripts/yahoo_probe.py")


if __name__ == "__main__":
    main()
