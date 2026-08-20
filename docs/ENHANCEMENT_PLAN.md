# Enhancement Plan — Claude Decide-Layer via Claude Code Routines

**Status: IMPLEMENTED (phases A + B).** Phase C is the user's Mac-side data entry
(README §2 step 10); phase D remains optional future work. This document specifies how to add a scheduled
Claude research layer to the existing lid-closed executor without weakening any of its
guarantees. The executor (`set_lineup.py` on the MacBook) stays the only thing that
touches Yahoo; Claude becomes an optional upstream brain whose failure mode is always
"the local optimizer runs instead."

---

## 0. Objective and non-negotiables

**Objective:** replace the dumb DECIDE stage input (Yahoo's single projection column)
with researched decisions — late-breaking injuries/inactives, weather, Vegas game
context, expert consensus — delivered automatically before every run, with a
human veto window.

**Non-negotiables carried over from the base system:**

1. Zero Claude dependency at execution time. If the research layer is down, late,
   or wrong, the Mac run proceeds exactly as today.
2. All §1.6 guardrails apply unchanged to researched swaps: startable/eligible/unlocked
   validation, dedup, verify-then-swap, PAUSED, login-guard.
3. No credentials move: the cloud session never sees Yahoo cookies, and the Mac
   never needs an Anthropic key.

## 1. Architecture

```
Claude Code Routine (cloud, fresh session per firing)
  Thu 20:00 UTC · Sun 13:00 UTC          ←  DST-safe: always ≥1.5h before the Mac runs
      │
      ├─ read repo: roster.json, league_settings.json, advice/history/
      ├─ research: WebSearch/WebFetch — injuries, inactives, weather, Vegas, consensus
      ├─ decide:  swaps within the roster, guardrail-aware, with rationale + confidence
      ├─ commit:  advice/lineup.json (+ advice/history/<week>.md)  → push to main
      └─ notify:  push notification to the iPhone (veto window opens)
      │
      ▼
GitHub repo (data channel — no new platform)
      │
      ▼
MacBook run.sh (Thu 17:30 / Sun 10:30 local)
      ├─ git fetch origin main && git show FETCH_HEAD:advice/lineup.json  (never merges)
      ├─ set_lineup.py override chain:
      │     1. local lineup.json   (manual, SSH-written — authoritative, as today)
      │     2. advice/lineup.json  (fresh remote advice — falls back if nothing validates)
      │     3. greedy optimizer    (unchanged)
      └─ WRITE → VERIFY → REPORT   (unchanged)
```

The repo doubles as the season-long memory: `advice/history/` accumulates each week's
rationale, and every Routine firing reads the previous entries before deciding.

## 2. Design decisions (the deep-think layer)

Each decision below exists because a naive version fails in a specific way.

**D1 — Separate advice file, not the manual `lineup.json`.**
`lineup.json` is gitignored local state for the human's SSH override; committing to the
same name would collide with it and change its trust level. The Routine writes tracked
`advice/lineup.json` instead. Priority: manual file beats remote advice beats optimizer —
the human always outranks the machine.

**D2 — Freshness by embedded timestamp, not mtime.**
`load_override()` uses file mtime today. A git checkout/extract sets mtime to *extraction
time*, so remote advice would always look fresh — Thursday's advice would pass the
staleness check on Sunday. The advice file therefore carries `generated_at` (ISO-8601 UTC)
inside the JSON, and the loader trusts that field. Remote advice gets a **6-hour** window
(scheduled ~1.5–2.5h before use; 6h guarantees one week's advice can never leak into the
next slate) while the manual file keeps its 20h mtime window.

**D3 — Different fallback semantics for remote vs manual advice.**
A fresh *manual* override is authoritative even when entries fail to match — the human
deliberately chose it, so the optimizer must not churn it (current behavior, kept).
A fresh *remote* advice file whose entries **all** fail validation is evidence the
research layer produced garbage (renamed players, hallucinated roster) — the executor
falls back to the optimizer instead of doing nothing. Partial matches apply the valid
subset, as today. An advice file with an empty `swaps` list means "lineup is right,
no changes" — represented explicitly as `{"swaps": [], "no_change": true, ...}` so the
executor can honor it (skip optimizer pass 2 churn) while still fixing BYE/OUT starters
via pass 1. Simplest safe version for phase 1: empty swaps → optimizer runs (unchanged
shape check); the explicit no-change contract is a phase-2 refinement.

**D4 — The Mac never merges.**
`run.sh` does `git fetch origin main` + `git show FETCH_HEAD:advice/lineup.json >
advice_remote.json` (`|| true`). No pull, no working-tree mutation, no conflict states
on an unattended machine, and a network failure degrades to stale-or-absent advice —
which D2 already handles. The extracted copy lives in a gitignored path.

**D5 — Roster knowledge: tracked `roster.json`.**
The cloud session cannot see Yahoo (no cookies — by design), so the roster must live in
the repo. Phase 1: the user maintains `roster.json` (player name exactly as Yahoo renders
it, primary position, and optionally NFL team), updating it after add/drops — a 30-second
edit in the GitHub app. Phase 2 (optional): the Mac auto-exports its parsed roster after
each run and pushes it with a fine-grained PAT (contents:write, this repo only), making
the file self-maintaining. Advice entries that name players not in `roster.json` are
refused by the Routine's own prompt AND caught by executor validation — two independent
layers.

**D6 — League settings must be tracked.**
`config.json` is gitignored (correctly — it holds the ntfy topic and healthchecks URL),
so the Routine's clone has no slot/eligibility knowledge. A new tracked
`league_settings.json` carries the shareable subset: slot list, `slot_eligibility`,
scoring type (std/half/PPR), roster size. `set_lineup.py` does not read it (avoids
drift risk in the executor); it is the Routine's input only, and the README documents
that the two eligibility maps must be kept in sync when the league changes.

**D7 — Name normalization at the matching boundary.**
Web sources write "D.J. Moore", Yahoo renders "DJ Moore". Override matching (both manual
and remote) normalizes both sides: casefold, strip non-alphanumerics, drop generational
suffixes (jr/sr/ii/iii/iv/v). An entry that normalizes to zero or multiple roster matches
is skipped and reported — never guessed.

**D8 — Schedule with DST margin.**
Routine crons are UTC; the NFL season spans the November fall-back (ET goes UTC-4 → UTC-5).
Fixed UTC times are chosen so both offsets land ≥1.5h before the Mac runs:

| Routine | Cron (UTC) | Fires ET (EDT / EST) | Mac run (local ET) | Margin |
|---|---|---|---|---|
| Thursday | `0 20 * * 4` | 16:00 / 15:00 | Thu 17:30 | 1.5h / 2.5h |
| Sunday | `0 13 * * 0` | 09:00 / 08:00 | Sun 10:30 | 1.5h / 2.5h |

Both firings stay on the same weekday in UTC as in ET, so the day-of-week fields are safe.
The margin is also the human **veto window** (D10).

**D9 — Make the repository private (recommended).**
The advice layer commits your planned lineup changes, with reasoning, up to 2.5 hours
before locks — in a public repo, that is free scouting for anyone in your league who
finds it. Recommendation: flip the repo to private. Consequence: the Mac's anonymous
HTTPS fetch stops working — it needs a read credential (fine-grained PAT with
contents:read, or a deploy key). This is the plan's main setup trade-off:
**public = zero-auth Mac fetch but leaks strategy; private = one-time Mac auth setup.**
If phase 2 (roster auto-push) happens, the Mac needs a write PAT anyway, so private
costs nothing extra at that point.

**D10 — Notification and veto flow.**
Each Routine firing ends with a push notification (Routine completion notifications)
summarizing the recommended swaps. Between the notification and the Mac run
(~1.5–2.5h) the human can: do nothing (advice executes), `touch PAUSED` via SSH
(nothing executes), or SSH-write a manual `lineup.json` (human override wins per D1).
The existing ntfy report after the Mac run closes the loop with what actually happened.

## 3. The Routines

Two Routines (Thu, Sun), **fresh session per firing** in this repo's cloud environment,
so each run starts from a clean clone of `main`. One shared prompt (stored as
`docs/ROUTINE_PROMPT.md` and pasted into the Routine config) with this contract:

**Inputs (read from the clone):** `roster.json`, `league_settings.json`,
`advice/history/` (most recent 2 entries), today's date.

**Research checklist (WebSearch/WebFetch):**
1. Injury/inactive status for every rostered player — official reports and beat
   reporters; anything fresher than Yahoo's badge matters most.
2. Confirmed inactives for tonight's/today's games if already published.
3. Weather for outdoor games involving rostered players (wind >15mph flags K and
   deep-ball WRs).
4. Vegas totals and spreads for rostered players' games (game-script context).
5. Start/sit consensus from at least two independent expert sources.
6. Kickoff times — flag any rostered player whose game locks before the Mac run
   (e.g. 9:30 ET international games) as UNSWAPPABLE.

**Output contract (the only writes allowed):**
- `advice/lineup.json`:
  `{"generated_at": "<ISO-8601Z>", "week": <n>, "swaps": [{"out": "...", "in": "...",
  "reason": "...", "confidence": "high|medium|low"}], "notes": "..."}` —
  names copied *exactly* from `roster.json`; only startable, non-BYE, non-locked
  players; each player at most once; empty `swaps` when the lineup is already right.
- `advice/history/week<NN>-<thu|sun>.md`: the full rationale (kept forever — this is
  the season memory).
- Commit both, push to `main`, retrying per the repo's push guidance.

**Hard rules in the prompt:** never invent players; never propose anyone the research
marks OUT/doubtful-inactive/BYE; when research is inconclusive or sources conflict,
prefer fewer swaps ("when in doubt, sit the advice out — the optimizer is the floor");
if the session cannot complete research, push nothing (absence = clean fallback).

## 4. Executor changes (small, test-covered)

1. **`run.sh`** — before invoking Python:
   `git fetch origin main && git show FETCH_HEAD:advice/lineup.json > advice_remote.json || true`
   (`advice_remote.json` gitignored).
2. **`set_lineup.py`** — `load_override()` becomes a chain: manual `lineup.json`
   (mtime freshness, 20h, authoritative) → `advice_remote.json` (`generated_at`
   freshness, 6h, falls back on zero validated entries) → `None`. Name-normalized
   matching (D7) applied in the override validation for both sources. `source`
   reported as `manual` / `advice` / `optimizer` in `last_status.json` and ntfy.
3. **Tests** — freshness-by-timestamp (including the mtime-fresh-but-timestamp-stale
   trap), priority chain, normalization matcher (DJ/D.J., suffixes, ambiguity → skip),
   zero-validated-remote → optimizer fallback, manual-authoritative preserved.
4. **New tracked files** — `advice/` (with `.gitkeep`), `roster.json` (seed template),
   `league_settings.json`, `docs/ROUTINE_PROMPT.md`; `.gitignore` adds `advice_remote.json`.

Nothing else in the executor changes; WRITE/VERIFY/REPORT and all guardrails untouched.

## 5. Rollout phases

| Phase | Where | Work | Exit criterion |
|---|---|---|---|
| **A** | This session | Repo changes of §4 + files of D5/D6 + prompt doc; tests green; push | CI green; user fills `roster.json` + `league_settings.json` |
| **B** | This session | Create both Routines (`create_trigger`, fresh-session, UTC crons of D8, push notifications on); **manually fire one** and verify a real session researches, commits `advice/lineup.json` to main, and the notification arrives | One live advice commit on `main` produced by a fired Routine |
| **C** | The Mac | `git pull` the executor changes; `DRY_RUN=1` run shows `source: advice` with the test advice; decide repo visibility (D9) and set up the read credential if private | Dry run consumes real Routine output end-to-end |
| **D** (optional) | Later | Roster auto-push from the Mac (write PAT); explicit no-change contract (D3); Monday-morning retrospective Routine that scores last week's advice vs actual points and appends to history | Self-maintaining roster; measurable advice quality |

## 6. New failure modes (and why each is safe)

| Failure | Result |
|---|---|
| Routine doesn't fire / session dies / push fails | No new advice commit → Mac extracts the old file → `generated_at` stale → optimizer. Absence is the designed failure mode. |
| Research hallucinates a player / wrong names | Routine prompt forbids it; normalization finds no unique match; entry skipped; zero-validated → optimizer (D3). |
| Advice proposes a player who got ruled OUT after generation | Executor's own `startable()` check (Yahoo's badge at run time) rejects the entry — the same guardrail that protects manual overrides. |
| Mac offline / GitHub unreachable at fetch time | `|| true` → stale-or-absent advice → optimizer. |
| Both Routine and optimizer disagree with the human | Human wrote `lineup.json` → it wins (D1); or `touch PAUSED` → nothing executes. |
| Two firings in one day (fall-back DST day) | UTC crons fire once per cron spec; wall-clock double-fire doesn't apply (D8 uses UTC). |

## 7. Cost & footprint

Two research sessions per week (each: a clone, ~10–20 web fetches/searches, one commit)
— minutes of cloud session time; no new services, no API keys to manage, no runtime
dependency added to the Mac. The repo gains ~2 small commits per week, which is also
the audit trail.

## 8. Decisions needed from the user before implementation

1. **Repo visibility** (D9): make private (recommended — requires Mac read auth), or
   accept the scouting leak and keep zero-auth fetch.
2. **Advice commits land on `main`** — confirm that's acceptable (alternative: a
   dedicated `advice` branch the Mac fetches; keeps `main` quiet, one extra setup knob).
3. **Schedule confirmation** (D8): Thu 20:00 / Sun 13:00 UTC, or shift.
4. **Go/no-go for phases A+B** from this session once 1–3 are answered.
