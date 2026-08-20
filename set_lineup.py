#!/usr/bin/env python3
"""
Yahoo Fantasy Football lineup setter — pure Playwright, headless, no Claude.
READ -> DECIDE -> WRITE (Swap Mode) -> VERIFY -> REPORT.
Exit codes: 0 ok/no-change/partial, 2 LOGIN_REQUIRED, 3 write error, 4 parse error.

Hard guardrails (encoded below, do not weaken):
  - position swaps only — never add/drop/trade
  - never start BYE/OUT/IR/SUSP players
  - skip locked players and report them
  - abort with ZERO writes on any login/sign-in page
  - idempotent verify-then-swap after every write
  - PAUSED file kills writes instantly
  - min-gain threshold prevents churn swaps
"""
import json, os, re, sys, time, datetime, pathlib, urllib.request

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = pathlib.Path(__file__).resolve().parent


def load_config():
    f = ROOT / "config.json"
    if not f.exists():
        sys.exit("config.json not found — copy config.example.json to config.json "
                 "and fill in your league/team ids, ntfy topic, and healthchecks URL.")
    return json.loads(f.read_text())


CFG  = load_config()
TS   = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
TEAM_URL = f"https://football.fantasysports.yahoo.com/f1/{CFG['league_id']}/{CFG['team_id']}"
DRY_RUN  = os.environ.get("DRY_RUN", "0") == "1"
PAUSED   = (ROOT / "PAUSED").exists()
BAD      = set(CFG["bad_statuses"])
STARTER_SLOTS_RE = re.compile(r"^(QB|RB|WR|TE|W/R/T|Q/W/R/T|K|DEF)$")

(ROOT / "logs").mkdir(exist_ok=True)
(ROOT / "screenshots").mkdir(exist_ok=True)


# ---------- reporting ----------
def notify(msg: str, prio: str = "default"):
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{CFG['ntfy_topic']}", data=msg.encode(),
            headers={"Title": "FFL Agent", "Priority": prio})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[notify-fail] {e}", file=sys.stderr)


def finish(status: str, extra: dict, code: int):
    out = {"status": status, "ts": TS, "dry_run": DRY_RUN, **extra}
    (ROOT / "logs" / "last_status.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out))
    prio = {"SUCCESS": "default", "NO_CHANGE": "default", "PARTIAL": "high",
            "LOGIN_REQUIRED": "urgent"}.get(status, "high")
    notify(f"{status}: {extra.get('summary','')}", prio)
    sys.exit(code)


# ---------- DOM CONTRACT (validate & pin in Phase 2 against live page) ----------
# Yahoo roster table rows expose per player:
#   slot label   : first cell text (QB/RB/WR/TE/W/R/T/K/DEF/BN/IR)
#   name         : anchor with class 'name' (a.name)
#   status badge : abbrev near name (Q, O, IR, D, SUSP, NA) — optional
#   bye          : 'Bye' column or opponent cell showing 'Bye'
#   proj points  : numeric cell in the projected/Fan Pts column
#   locked       : slot control disabled / lock glyph once game started
# Strategy: parse loosely by row text; interact via role/text locators.
ROW_SEL = "table tbody tr"


def parse_float(s):
    m = re.search(r"-?\d+(\.\d+)?", s or "")
    return float(m.group()) if m else 0.0


def parse_roster(page):
    page.wait_for_selector(ROW_SEL, timeout=20000)
    players = []
    for row in page.locator(ROW_SEL).all():
        txt = " ".join((row.inner_text() or "").split())
        name_loc = row.locator("a.name").first
        if name_loc.count() == 0:
            continue                                # empty slot / header row
        name = name_loc.inner_text().strip()
        cells = [c.strip() for c in row.locator("td, th").all_inner_texts()]
        slot = next((c for c in cells if STARTER_SLOTS_RE.match(c) or c in ("BN", "IR")), None)
        if not slot:
            continue
        status = next((s for s in ("IR", "SUSP", "PUP", "NFI", "NA", "O", "D", "Q")
                       if re.search(rf"\b{s}\b", txt)), "")
        bye = bool(re.search(r"\bBye\b", txt, re.I))
        proj = 0.0
        nums = [parse_float(c) for c in cells if re.fullmatch(r"-?\d+(\.\d+)?", c or "")]
        if nums:
            proj = nums[0]                          # Phase 2: pin exact proj column index
        locked = bool(re.search(r"\block", txt, re.I)) or "disabled" in (
            row.locator("td").first.get_attribute("class") or "")
        # primary position = first eligibility key found in row text
        primary = next((p for p in CFG["slot_eligibility"] if re.search(rf"\b{p}\b", txt)), None)
        players.append({"name": name, "slot": slot, "primary": primary,
                        "status": status, "bye": bye, "proj": proj, "locked": locked})
    if not players:
        finish("ERROR", {"summary": "roster parse returned 0 players — DOM changed?"}, 4)
    return players


def guard_login(page):
    url = page.url.lower()
    bad_url = "login.yahoo.com" in url or "/account/" in url
    has_pw  = page.locator("input[type=password], input[name=password]").count() > 0
    if bad_url or has_pw:
        page.screenshot(path=str(ROOT / "screenshots" / f"login_{TS}.png"))
        finish("LOGIN_REQUIRED",
               {"summary": "Yahoo session expired/challenged. Zero changes made. "
                           "Re-run seed_login.py (lid open) to re-seed."}, 2)


# ---------- decide ----------
def eligible(bench_p, slot):
    return bench_p["primary"] and slot in CFG["slot_eligibility"].get(bench_p["primary"], [])


def startable(p):
    return not p["bye"] and p["status"] not in BAD


def load_override():
    f = ROOT / "lineup.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h > CFG["lineup_json_max_age_hours"]:
            return None
        swaps = data.get("swaps")                   # [{"out": "...", "in": "..."}]
        if (isinstance(swaps, list) and swaps
                and all(isinstance(s, dict) for s in swaps)):
            return swaps
        return None                                 # wrong shape → optimizer fallback
    except Exception:
        return None


def plan_swaps(roster):
    """Return ordered [(starter_name, bench_name, slot, gain)]; conservative greedy."""
    swaps, used = [], set()
    starters = [p for p in roster if STARTER_SLOTS_RE.match(p["slot"])]
    bench    = [p for p in roster if p["slot"] == "BN"]
    # Pass 1: replace unstartable starters (BYE/OUT/etc.)
    for st in starters:
        if startable(st):
            continue
        cands = [b for b in bench if b["name"] not in used and startable(b)
                 and not b["locked"] and eligible(b, st["slot"])]
        if cands:
            best = max(cands, key=lambda b: b["proj"])
            swaps.append((st["name"], best["name"], st["slot"], round(best["proj"] - st["proj"], 1)))
            used.add(best["name"])
    # Pass 2: projection upgrades above threshold
    for st in sorted(starters, key=lambda p: p["proj"]):
        if not startable(st) or st["locked"]:
            continue
        cands = [b for b in bench if b["name"] not in used and startable(b)
                 and not b["locked"] and eligible(b, st["slot"])
                 and b["proj"] >= st["proj"] + CFG["min_swap_gain"]]
        if cands:
            best = max(cands, key=lambda b: b["proj"])
            swaps.append((st["name"], best["name"], st["slot"], round(best["proj"] - st["proj"], 1)))
            used.add(best["name"])
    return swaps


# ---------- write (Swap Mode) ----------
def player_row(page, name):
    return page.locator(ROW_SEL).filter(has=page.locator("a.name", has_text=name)).first


def click_slot_control(page, name):
    """Click the position/slot control in the player's row (enters/uses Swap Mode)."""
    row = player_row(page, name)
    ctl = row.get_by_role("button").first
    if ctl.count() == 0:
        ctl = row.locator("td").first                # Phase 2: pin exact control
    ctl.click(timeout=8000)


def enter_swap_mode(page):
    sm = page.get_by_text(re.compile(r"swap mode", re.I)).first
    if sm.count():
        sm.click(timeout=8000)
        page.wait_for_timeout(1200)


def execute(page, swaps):
    done, skipped = [], []
    enter_swap_mode(page)
    dirty = False        # a failed swap can leave a dangling Swap Mode selection;
                         # the next click would then EXECUTE an unplanned swap
    for (out_name, in_name, slot, gain) in swaps:
        try:
            if dirty:
                # hard reset of UI state: reload the page so no half-completed
                # selection can turn our next click into an unverified write
                page.goto(TEAM_URL, wait_until="domcontentloaded", timeout=45000)
                enter_swap_mode(page)
                dirty = False
            # pre-check: the starter must still hold the slot we planned against
            current = {p["name"]: p for p in parse_roster(page)}
            if current.get(out_name, {}).get("slot") != slot:
                skipped.append(f"{slot}: {out_name}→{in_name} (roster changed, pre-check)")
                continue
            click_slot_control(page, out_name)       # select starter to replace
            page.wait_for_timeout(900)               # human-paced; highlights render
            click_slot_control(page, in_name)        # click highlighted bench player
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(900)
            # verify-then-continue: re-parse and confirm in_name now holds a starter slot
            fresh = {p["name"]: p for p in parse_roster(page)}
            if fresh.get(in_name, {}).get("slot") == slot:
                done.append(f"{slot}: {out_name} → {in_name} (+{gain})")
            else:
                skipped.append(f"{slot}: {out_name}→{in_name} (verify failed)")
                dirty = True
        except PWTimeout:
            skipped.append(f"{slot}: {out_name}→{in_name} (locked/timeout)")
            dirty = True
        except Exception as e:
            skipped.append(f"{slot}: {out_name}→{in_name} ({type(e).__name__})")
            dirty = True
    return done, skipped


# ---------- main ----------
def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(ROOT / "profile"), headless=True,
            viewport={"width": 1366, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(TEAM_URL, wait_until="domcontentloaded", timeout=45000)
        guard_login(page)
        page.screenshot(path=str(ROOT / "screenshots" / f"before_{TS}.png"), full_page=True)

        roster = parse_roster(page)
        override = load_override()
        if override:
            swaps = []
            by_name = {p["name"]: p for p in roster}
            for s in override:
                st, bn = by_name.get(s.get("out")), by_name.get(s.get("in"))
                if st and bn and startable(bn) and not bn["locked"] and eligible(bn, st["slot"]):
                    swaps.append((st["name"], bn["name"], st["slot"], round(bn["proj"]-st["proj"],1)))
            source = "lineup.json"
        else:
            swaps = plan_swaps(roster)
            source = "optimizer"

        if not swaps:
            page.screenshot(path=str(ROOT / "screenshots" / f"after_{TS}.png"), full_page=True)
            ctx.close()
            finish("NO_CHANGE", {"summary": f"lineup already optimal ({source})",
                                 "source": source}, 0)

        plan_txt = "; ".join(f"{s[2]}: {s[0]}→{s[1]} (+{s[3]})" for s in swaps)
        if DRY_RUN or PAUSED:
            ctx.close()
            finish("NO_CHANGE", {"summary": f"[DRY] planned: {plan_txt}",
                                 "planned": plan_txt, "source": source}, 0)

        done, skipped = execute(page, swaps)
        page.goto(TEAM_URL, wait_until="domcontentloaded")
        page.screenshot(path=str(ROOT / "screenshots" / f"after_{TS}.png"), full_page=True)
        ctx.close()

    if done and not skipped:
        finish("SUCCESS", {"summary": "; ".join(done), "source": source}, 0)
    elif done:
        finish("PARTIAL", {"summary": f"done: {'; '.join(done)} | skipped: {'; '.join(skipped)}",
                           "source": source}, 0)
    else:
        finish("PARTIAL", {"summary": f"no swaps applied | skipped: {'; '.join(skipped)}",
                           "source": source}, 3)


if __name__ == "__main__":
    main()
