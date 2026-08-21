#!/usr/bin/env python3
"""Yahoo Fantasy Sports API client — OAuth2 reads and lineup writes (fspt-w).

Shared by the executor (set_lineup.py write_path=api), the scripts/ tooling,
and the MCP connector. Stdlib only — no new runtime dependencies (plan N6).

POSITION SWAPS ONLY (plan N1): this module deliberately contains no code for
Yahoo transaction endpoints (add/drop/trade/waiver claims) and never will.
tests/test_yahoo_api.py greps this source to enforce that.

Field-mapping status (plan P0.T3): selected_position, eligible_positions,
status, bye_weeks and the roster XML PUT shape follow Yahoo's documented API.
Two fields are marked TODO(P0.T3) because only a live probe can pin them:
the per-player editability/locked flag and projected-points availability.
Until pinned, locked defaults to False (safe: Yahoo's roster PUT is ATOMIC
per request, so with one PUT per swap a locked player fails only its own
swap, which is reported as skipped — plan N7 verify still governs) and proj defaults
to 0.0 with PROJ_AVAILABLE=False (safe: the optimizer degrades to pass 1,
which still fixes BYE/OUT starters).
"""
import fcntl
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape

API_BASE   = "https://fantasysports.yahooapis.com/fantasy/v2"
AUTH_URL   = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL  = "https://api.login.yahoo.com/oauth2/get_token"
NFL_GAME   = "nfl"

DEFAULT_SECRETS_PATH = pathlib.Path(
    os.environ.get("YAHOO_SECRETS_PATH", "~/.ffl-secrets/yahoo.json")).expanduser()

# Statuses that are never startable — mirrors config.example.json bad_statuses.
BAD_STATUSES = {"O", "IR", "SUSP", "NA", "PUP", "NFI"}

PROJ_AVAILABLE = False   # flipped True at runtime if projected points are found


class YahooApiError(Exception):
    """Non-auth API failure after retries."""


class AuthExpired(YahooApiError):
    """Refresh failed — the human must re-run scripts/yahoo_auth.py."""


class LockedError(YahooApiError):
    """Yahoo refused a position change for a locked/started player."""


# Substrings observed in Yahoo error payloads for locked players. P0.T4 must
# append the real strings seen live to docs/api_notes.md and extend this set.
_LOCKED_MARKERS = ("lock", "game has started", "cannot move", "not editable")


# ---------------------------------------------------------------- token store
class TokenStore:
    """Loads, refreshes, and persists OAuth tokens. File format:
    {client_id, client_secret, access_token, refresh_token, expires_at,
     team_key?}  — chmod 600, NEVER committed (plan N5)."""

    def __init__(self, path=None):
        self.path = pathlib.Path(path or DEFAULT_SECRETS_PATH)
        if not self.path.exists():
            raise AuthExpired(
                f"{self.path} not found — run scripts/yahoo_auth.py first.")
        self._data = json.loads(self.path.read_text())

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set_and_save(self, **kv):
        self._data.update(kv)
        self._save()

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def access_token(self):
        if time.time() > float(self._data.get("expires_at", 0)) - 60:
            self._refresh()
        return self._data["access_token"]

    def _refresh(self):
        lock_path = self.path.with_suffix(".lock")
        with open(lock_path, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                # another process may have refreshed while we waited
                self._data = json.loads(self.path.read_text())
                if time.time() <= float(self._data.get("expires_at", 0)) - 60:
                    return
                body = urllib.parse.urlencode({
                    "client_id": self._data["client_id"],
                    "client_secret": self._data["client_secret"],
                    "refresh_token": self._data["refresh_token"],
                    "grant_type": "refresh_token",
                    "redirect_uri": self._data.get("redirect_uri", "oob"),
                }).encode()
                req = urllib.request.Request(
                    TOKEN_URL, data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        tok = json.loads(r.read().decode())
                except urllib.error.HTTPError as e:
                    raise AuthExpired(
                        f"token refresh rejected (HTTP {e.code}) — "
                        "re-run scripts/yahoo_auth.py") from e
                except OSError as e:
                    raise YahooApiError(f"token refresh network failure: {e}") from e
                self._data["access_token"] = tok["access_token"]
                self._data["refresh_token"] = tok.get(
                    "refresh_token", self._data["refresh_token"])
                self._data["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
                self._save()
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)


# --------------------------------------------------------------- JSON walking
def _find_all(obj, key):
    """Yahoo's JSON nests dicts inside numeric-keyed dicts inside lists.
    Recursively collect every value stored under `key`."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            out.extend(_find_all(v, key))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_find_all(v, key))
    return out


def _first(obj, key, default=None):
    found = _find_all(obj, key)
    return found[0] if found else default


def _coverage_week(obj):
    """The roster's week must come from a coverage block (a dict carrying
    coverage_type == 'week' alongside 'week') — a bare _first(data, 'week')
    can land on a player's bye_weeks first and poison every bye comparison."""
    if isinstance(obj, dict):
        if obj.get("coverage_type") == "week" and "week" in obj:
            return obj["week"]
        for v in obj.values():
            w = _coverage_week(v)
            if w is not None:
                return w
    elif isinstance(obj, list):
        for v in obj:
            w = _coverage_week(v)
            if w is not None:
                return w
    return None


# -------------------------------------------------------------------- client
class YahooApi:
    def __init__(self, tokens: TokenStore = None):
        self.tokens = tokens or TokenStore()
        self.team_key = self.tokens.get("team_key")   # cached after discovery
        self.current_week = None                      # filled by read_roster

    # ---- transport ----
    def _request(self, path, method="GET", body=None, content_type=None):
        url = f"{API_BASE}{path}"
        url += ("&" if "?" in url else "?") + "format=json"
        for attempt, delay in enumerate((0, 2, 4, 8)):
            if delay:
                time.sleep(delay)
            headers = {"Authorization": f"Bearer {self.tokens.access_token()}"}
            if content_type:
                headers["Content-Type"] = content_type
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode() or "{}")
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode(errors="replace")
                except Exception:
                    pass
                if e.code == 401 and attempt == 0:
                    self.tokens._refresh()            # one forced refresh, then retry
                    continue
                if e.code == 401:
                    raise AuthExpired("401 after refresh — re-run scripts/yahoo_auth.py")
                low = detail.lower()
                if e.code == 400 and any(m in low for m in _LOCKED_MARKERS):
                    raise LockedError(detail[:300])
                if e.code in (429, 999):
                    continue                          # backoff loop
                raise YahooApiError(f"HTTP {e.code} on {method} {path}: {detail[:300]}")
            except OSError as e:
                if attempt == 3:
                    raise YahooApiError(f"network failure on {path}: {e}")
        raise YahooApiError(f"gave up after retries: {method} {path}")

    # ---- discovery ----
    def discover_team_key(self):
        if self.team_key:
            return self.team_key
        data = self._request(f"/users;use_login=1/games;game_keys={NFL_GAME}/teams")
        keys = [k for k in _find_all(data, "team_key") if isinstance(k, str)]
        if not keys:
            raise YahooApiError("no NFL team found for this Yahoo login")
        self.team_key = keys[0]
        self.tokens.set_and_save(team_key=self.team_key)
        return self.team_key

    @property
    def league_key(self):
        # team_key format: {game_id}.l.{league_id}.t.{team_id}
        return ".".join(self.discover_team_key().split(".")[:3])

    # ---- reads ----
    def read_roster(self, week=None):
        """Return players in the executor's exact shape:
        {name, slot, primary, status, bye, proj, locked} (+ player_key)."""
        global PROJ_AVAILABLE
        tk = self.discover_team_key()
        path = f"/team/{tk}/roster" + (f";week={week}" if week else "") + "/players"
        data = self._request(path)
        self.current_week = week or _coverage_week(data) or self.current_week
        try:
            self.current_week = int(self.current_week)
        except (TypeError, ValueError):
            pass
        players, raw_players = [], _find_all(data, "player")
        seen_keys = set()
        for p in raw_players:
            pk = _first(p, "player_key")
            name = _first(p, "full")
            if not pk or not name or pk in seen_keys:
                continue
            seen_keys.add(pk)
            slot = _first(p, "selected_position") or {}
            slot_pos = _first(slot, "position") if isinstance(slot, (dict, list)) else slot
            status = str(_first(p, "status") or "")
            # normalize composite statuses like "PUP-R" -> "PUP"
            status = status.split("-")[0] if status else ""
            bye_wk = _first(p, "bye_weeks")
            bye_wk = _first(bye_wk, "week") if bye_wk is not None else None
            bye = False
            try:
                bye = int(bye_wk) == int(self.current_week)
            except (TypeError, ValueError):
                pass
            proj = _first(p, "projected_points")
            if proj is not None:
                proj = float(_first(proj, "total") or 0.0) if isinstance(proj, (dict, list)) else float(proj)
                PROJ_AVAILABLE = True
            else:
                proj = 0.0            # TODO(P0.T3): pin the projected-points field
            editable = _first(p, "is_editable")
            locked = (str(editable) == "0") if editable is not None else False
            # TODO(P0.T3): pin the real editability/locked field name
            primary = _first(p, "primary_position") or \
                (str(_first(p, "display_position") or "").split(",")[0].strip())
            players.append({"name": str(name), "slot": str(slot_pos or ""),
                            "primary": primary or None, "status": status,
                            "bye": bye, "proj": proj, "locked": locked,
                            "player_key": pk})
        return players

    def read_matchup(self, week=None):
        tk = self.discover_team_key()
        path = f"/team/{tk}/matchups" + (f";weeks={week}" if week else "")
        return self._request(path)

    def read_free_agents(self, position=None, count=25):
        path = f"/league/{self.league_key}/players;status=FA;sort=PTS;count={int(count)}"
        if position:
            path += f";position={urllib.parse.quote(str(position))}"
        return self._request(path)

    def read_league_settings(self):
        return self._request(f"/league/{self.league_key}/settings")

    # ---- writes (position changes ONLY — see module docstring) ----
    @staticmethod
    def build_roster_xml(changes, week):
        """changes: [(player_key, new_position)] -> the documented PUT body."""
        rows = "".join(
            f"<player><player_key>{escape(str(pk))}</player_key>"
            f"<position>{escape(str(pos))}</position></player>"
            for pk, pos in changes)
        return ('<?xml version="1.0"?><fantasy_content><roster>'
                f"<coverage_type>week</coverage_type><week>{int(week)}</week>"
                f"<players>{rows}</players></roster></fantasy_content>").encode()

    def set_positions(self, changes, week):
        """PUT position changes. Raises LockedError / YahooApiError; the caller
        must verify by re-reading the roster (plan N7)."""
        if not changes:
            return
        tk = self.discover_team_key()
        self._request(f"/team/{tk}/roster", method="PUT",
                      body=self.build_roster_xml(changes, week),
                      content_type="application/xml")


# ------------------------------------------------- decide() -> API bridging
def changes_for_swap(out_name, in_name, slot, roster):
    """Map one decide() swap onto [(player_key, position)] changes:
    the bench player takes `slot`, the outgoing starter goes to BN.
    Raises KeyError if either name has no unique roster match."""
    from ffl_common import norm_name, roster_index
    idx = roster_index(roster)
    st, bn = idx.get(norm_name(out_name)), idx.get(norm_name(in_name))
    if not st or not bn:
        raise KeyError(f"no unique roster match for {out_name}→{in_name}")
    return [(bn["player_key"], slot), (st["player_key"], "BN")]
