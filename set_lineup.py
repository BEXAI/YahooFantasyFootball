#!/usr/bin/env python3
"""
Yahoo Fantasy Football lineup setter — pure Playwright, headless, no Claude.
READ -> DECIDE -> WRITE (Swap Mode) -> VERIFY -> REPORT.
Exit codes: 0 ok/no-change/partial, 2 LOGIN_REQUIRED, 3 write error, 4 parse error.

DECIDE sources, in priority order (see docs/ENHANCEMENT_PLAN.md):
  manual    lineup.json (SSH-written, mtime-fresh)  — authoritative
  advice    advice_remote.json (Routine-committed, generated_at-fresh)
  optimizer greedy two-pass fallback — always available

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
        # match status/lock/position against row text WITHOUT the player's name:
        # names like "Aidan O'Connell" match \bO\b (→ falsely OUT) and
        # "Drew Lock" matches \block (→ falsely locked) otherwise
        meta = " ".join(txt.replace(name, " ").split())
        status = next((s for s in ("IR", "SUSP", "PUP", "NFI", "NA", "O", "D", "Q")
                       if re.search(rf"\b{s}\b", meta)), "")
        bye = bool(re.search(r"\bBye\b", meta, re.I))
        proj = 0.0
        nums = [parse_float(c) for c in cells if re.fullmatch(r"-?\d+(\.\d+)?", c or "")]
        if nums:
            proj = nums[0]                          # Phase 2: pin exact proj column index
        locked = bool(re.search(r"\block", meta, re.I)) or "disabled" in (
            row.locator("td").first.get_attribute("class") or "")
        # primary position = first eligibility key found in row text
        primary = next((p for p in CFG["slot_eligibility"] if re.search(rf"\b{p}\b", meta)), None)
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


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _valid_swap_list(data):
    if not isinstance(data, dict):
        return None
    swaps = data.get("swaps")                       # [{"out": "...", "in": "..."}]
    if (isinstance(swaps, list) and swaps
            and all(isinstance(s, dict) for s in swaps)):
        return swaps
    return None                                     # wrong/empty shape → next source


def load_manual_override():
    """Human-written lineup.json (SSH). Fresh (mtime) => AUTHORITATIVE."""
    f = ROOT / "lineup.json"
    try:
        age_h = (time.time() - f.stat().st_mtime) / 3600
    except OSError:                 # missing, or deleted between runs — no override
        return None
    if age_h > CFG.get("lineup_json_max_age_hours", 20):
        return None
    return _valid_swap_list(_read_json(f))


def load_advice():
    """Routine-committed advice extracted by run.sh into advice_remote.json.
    Freshness comes from the embedded generated_at, never mtime — git extraction
    resets mtime to extraction time, which would make stale advice look fresh."""
    f = ROOT / "advice_remote.json"
    if not f.exists():
        return None
    data = _read_json(f)
    if not isinstance(data, dict):
        return None
    try:
        gen = datetime.datetime.fromisoformat(
            str(data.get("generated_at")).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=datetime.timezone.utc)
    age_h = (datetime.datetime.now(datetime.timezone.utc) - gen).total_seconds() / 3600
    # Reject future timestamps too (beyond 1h clock-skew tolerance): a wrong
    # future generated_at would otherwise stay "fresh" for weeks and defeat
    # the entire freshness mechanism (Thursday's advice running every run).
    if age_h < -1 or age_h > CFG.get("advice_max_age_hours", 6):
        return None
    return _valid_swap_list(data)


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(s):
    """Match names across renderings: 'D.J. Moore' == 'DJ Moore', drop Jr/III etc.
    Total over any input: a non-string (LLM-generated advice can carry wrong
    types) normalizes to "" and simply never matches — it must not crash."""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"[^a-z0-9 ]", "", s.casefold())
    return " ".join(p for p in s.split() if p not in _SUFFIXES)


def roster_index(roster):
    """normalized name -> player; a normalized-name collision maps to None so an
    ambiguous override entry is skipped, never guessed."""
    idx = {}
    for p in roster:
        k = norm_name(p["name"])
        idx[k] = None if k in idx else p
    return idx


def build_override_swaps(roster, entries):
    """Validate override entries (manual or advice) against the parsed roster.
    Same guardrails as the optimizer: startable, unlocked, slot-eligible, and
    each player in at most one swap. Returns (swaps, skipped): rejected entries
    are reported with a reason, never silently dropped."""
    idx = roster_index(roster)
    swaps, used, skipped = [], set(), []
    for s in entries or []:
        out_raw, in_raw = s.get("out"), s.get("in")
        label = f"{out_raw}→{in_raw}"
        st, bn = idx.get(norm_name(out_raw)), idx.get(norm_name(in_raw))
        if st is None or bn is None:
            skipped.append(f"{label} (no unique roster match)")
        elif st is bn:
            skipped.append(f"{label} (self-swap)")
        elif st["name"] in used or bn["name"] in used:
            skipped.append(f"{label} (player already used)")
        elif bn["slot"] != "BN":
            # contract (README §1.3, ROUTINE_PROMPT): 'in' must come from the
            # bench — a starter or IR-slot player here would make execute()
            # click an unplanned, unverifiable UI interaction
            skipped.append(f"{label} (swap-in not on bench)")
        elif not startable(bn):
            skipped.append(f"{label} (not startable)")
        elif bn["locked"]:
            skipped.append(f"{label} (locked)")
        elif not eligible(bn, st["slot"]):
            skipped.append(f"{label} (ineligible for {st['slot']})")
        else:
            swaps.append((st["name"], bn["name"], st["slot"],
                          round(bn["proj"] - st["proj"], 1)))
            used.update((st["name"], bn["name"]))
    return swaps, skipped


def decide(roster):
    """Decision chain: manual override > fresh advice > optimizer.
    Manual is authoritative even when nothing validates (the human chose it);
    advice with ZERO validated entries is treated as garbage and falls back.
    Returns (swaps, source, override_skipped)."""
    manual = load_manual_override()
    if manual is not None:
        swaps, skipped = build_override_swaps(roster, manual)
        return swaps, "manual", skipped
    adv_skipped = []
    advice = load_advice()
    if advice:
        swaps, adv_skipped = build_override_swaps(roster, advice)
        if swaps:
            return swaps, "advice", adv_skipped
    return plan_swaps(roster), "optimizer", adv_skipped


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
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass    # busy pages may never go idle; both clicks already fired,
                        # so the verify re-parse below is the authoritative check
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
        swaps, source, osk = decide(roster)
        osk_txt = f" | override skipped: {'; '.join(osk)}" if osk else ""
        extra = {"source": source, **({"override_skipped": osk} if osk else {})}

        if not swaps:
            page.screenshot(path=str(ROOT / "screenshots" / f"after_{TS}.png"), full_page=True)
            ctx.close()
            finish("NO_CHANGE", {"summary": f"lineup already optimal ({source}){osk_txt}",
                                 **extra}, 0)

        plan_txt = "; ".join(f"{s[2]}: {s[0]}→{s[1]} (+{s[3]})" for s in swaps)
        if DRY_RUN or PAUSED:
            tag = "DRY" if DRY_RUN else "PAUSED"
            ctx.close()
            finish("NO_CHANGE", {"summary": f"[{tag}] planned: {plan_txt}{osk_txt}",
                                 "planned": plan_txt, **extra}, 0)

        done, skipped = execute(page, swaps)
        try:
            # best-effort evidence: never let a nav/screenshot failure crash us
            # AFTER live writes — the finish() report below must still happen
            page.goto(TEAM_URL, wait_until="domcontentloaded", timeout=45000)
            page.screenshot(path=str(ROOT / "screenshots" / f"after_{TS}.png"), full_page=True)
        except Exception as e:
            print(f"[after-screenshot-fail] {e}", file=sys.stderr)
        try:
            ctx.close()
        except Exception as e:
            print(f"[ctx-close-fail] {e}", file=sys.stderr)

    if done and not skipped:
        finish("SUCCESS", {"summary": f"{'; '.join(done)}{osk_txt}", **extra}, 0)
    elif done:
        finish("PARTIAL", {"summary": f"done: {'; '.join(done)} | skipped: "
                                      f"{'; '.join(skipped)}{osk_txt}", **extra}, 0)
    else:
        finish("PARTIAL", {"summary": f"no swaps applied | skipped: "
                                      f"{'; '.join(skipped)}{osk_txt}", **extra}, 3)


if __name__ == "__main__":
    main()
