"""Tests for yahoo_api.py and the executor's API write path (plan P1/P2).

No live calls: HTTP is mocked, fixtures are synthetic (shaped like Yahoo's
nested numeric-keyed JSON) until scripts/yahoo_probe.py pins the real payload.
"""
import io
import json
import pathlib
import subprocess
import time
import urllib.error

import pytest

import yahoo_api
from yahoo_api import (TokenStore, YahooApi, AuthExpired, LockedError,
                       YahooApiError, _find_all, _first, _coverage_week,
                       changes_for_swap)

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------- plan N1: position swaps only, forever ----------

def test_no_transaction_endpoints_in_api_client():
    src = (ROOT / "yahoo_api.py").read_text()
    assert "/transactions" not in src, "N1 violated: transaction endpoint in API client"


def test_no_transaction_endpoints_in_connector():
    src = (ROOT / "connector" / "server.py").read_text()
    assert "/transactions" not in src, "N1 violated: transaction endpoint in connector"


# ---------- plan N5: no committed secrets ----------

def test_no_secret_material_in_tracked_files():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True).stdout.split()
    for f in tracked:
        p = ROOT / f
        if p.suffix in (".png", ".jpg") or not p.exists():
            continue
        text = p.read_text(errors="ignore")
        for marker in ("\"access_token\": \"ey", "\"refresh_token\": \"A",
                       "client_secret\": \"0"):
            assert marker not in text, f"possible committed secret in {f}"


# ---------- JSON walking ----------

NESTED = {"a": [{"b": {"player_key": "k1"}}, {"player_key": "k2", "c": {"player_key": "k3"}}]}


def test_find_all_walks_nested_lists_and_dicts():
    assert _find_all(NESTED, "player_key") == ["k1", "k2", "k3"]
    assert _first(NESTED, "player_key") == "k1"
    assert _first(NESTED, "missing", "dflt") == "dflt"


def test_coverage_week_ignores_bye_weeks():
    data = {"roster": {"0": {"players": {"0": {"player": [
        [{"bye_weeks": {"week": "7"}}],
        {"selected_position": [{"coverage_type": "week", "week": "2"},
                               {"position": "RB"}]},
    ]}}}}}
    assert _coverage_week(data) == "2"      # never the bye week


# ---------- TokenStore ----------

def _secrets_file(tmp_path, expires_delta):
    p = tmp_path / "yahoo.json"
    p.write_text(json.dumps({
        "client_id": "cid", "client_secret": "cs",
        "access_token": "OLD", "refresh_token": "R1",
        "expires_at": time.time() + expires_delta}))
    return p


def test_tokenstore_missing_file_raises_authexpired(tmp_path):
    with pytest.raises(AuthExpired):
        TokenStore(tmp_path / "nope.json")


def test_tokenstore_fresh_token_used_without_refresh(tmp_path, monkeypatch):
    ts = TokenStore(_secrets_file(tmp_path, 3600))
    monkeypatch.setattr(yahoo_api.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no refresh expected")))
    assert ts.access_token() == "OLD"


def test_tokenstore_refresh_rotates_and_persists(tmp_path, monkeypatch):
    path = _secrets_file(tmp_path, -10)          # expired

    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        return FakeResp(json.dumps({"access_token": "NEW", "refresh_token": "R2",
                                    "expires_in": 3600}).encode())
    monkeypatch.setattr(yahoo_api.urllib.request, "urlopen", fake_urlopen)
    ts = TokenStore(path)
    assert ts.access_token() == "NEW"
    saved = json.loads(path.read_text())
    assert saved["refresh_token"] == "R2" and saved["access_token"] == "NEW"


def test_tokenstore_refresh_rejection_is_authexpired(tmp_path, monkeypatch):
    path = _secrets_file(tmp_path, -10)

    def fake_urlopen(req, timeout=0):
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(yahoo_api.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(AuthExpired):
        TokenStore(path).access_token()


def test_tokenstore_corrupt_file_is_authexpired(tmp_path):
    p = tmp_path / "yahoo.json"
    p.write_text("{truncated")
    with pytest.raises(AuthExpired):
        TokenStore(p)


def test_tokenstore_incomplete_secrets_is_authexpired_not_keyerror(tmp_path):
    # the exact on-disk state P0.T1 creates before yahoo_auth.py has run
    p = tmp_path / "yahoo.json"
    p.write_text(json.dumps({"client_id": "cid", "client_secret": "cs"}))
    with pytest.raises(AuthExpired):
        TokenStore(p).access_token()


def test_forced_refresh_recovers_from_surprise_401(tmp_path, monkeypatch):
    """A revoked-but-unexpired token must trigger a REAL refresh, then succeed."""
    path = _secrets_file(tmp_path, 3000)          # expires_at still looks valid
    calls = []

    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        calls.append(url)
        if url.startswith(yahoo_api.TOKEN_URL):
            return FakeResp(json.dumps({"access_token": "FRESH",
                                        "refresh_token": "R2",
                                        "expires_in": 3600}).encode())
        auth = req.headers.get("Authorization")
        if auth == "Bearer FRESH":
            return FakeResp(b'{"ok": true}')
        raise urllib.error.HTTPError(url, 401, "revoked", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(yahoo_api.urllib.request, "urlopen", fake_urlopen)
    api = YahooApi(tokens=TokenStore(path))
    api.team_key = "461.l.9.t.3"
    assert api._request("/x") == {"ok": True}
    assert any(u.startswith(yahoo_api.TOKEN_URL) for u in calls), \
        "401 recovery never contacted the token endpoint"


# ---------- request error taxonomy ----------

class FakeTokens:
    def __init__(self): self.d = {"team_key": "461.l.9.t.3"}
    def get(self, k, default=None): return self.d.get(k, default)
    def set_and_save(self, **kv): self.d.update(kv)
    def access_token(self): return "T"
    def _refresh(self, force=False): self.d["refreshed"] = force


def _api():
    a = YahooApi.__new__(YahooApi)
    a.tokens = FakeTokens()
    a.team_key = a.tokens.get("team_key")
    a.current_week = None
    return a


def _http_error(code, body=b"{}"):
    def raiser(req, timeout=0):
        raise urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))
    return raiser


def test_request_locked_marker_maps_to_lockederror(monkeypatch):
    monkeypatch.setattr(yahoo_api.urllib.request, "urlopen",
                        _http_error(400, b"player game has started"))
    with pytest.raises(LockedError):
        _api()._request("/x")


def test_request_plain_400_is_yahooapierror_not_locked(monkeypatch):
    monkeypatch.setattr(yahoo_api.urllib.request, "urlopen",
                        _http_error(400, b"invalid position for player"))
    with pytest.raises(YahooApiError) as e:
        _api()._request("/x")
    assert not isinstance(e.value, LockedError)


def test_request_401_twice_is_authexpired_after_one_refresh(monkeypatch):
    monkeypatch.setattr(yahoo_api.urllib.request, "urlopen", _http_error(401))
    api = _api()
    with pytest.raises(AuthExpired):
        api._request("/x")
    assert api.tokens.d.get("refreshed") is True


# ---------- roster mapping ----------

FIXTURE = {"fantasy_content": {"team": [
    [{"team_key": "461.l.9.t.3"}],
    {"roster": {"coverage_type": "week", "week": "2", "0": {"players": {
        "0": {"player": [
            [{"player_key": "461.p.100"}, {"name": {"full": "A Starter"}},
             {"display_position": "RB"}, {"primary_position": "RB"},
             {"bye_weeks": {"week": "7"}}, {"is_editable": 1}],
            {"selected_position": [{"coverage_type": "week", "week": "2"},
                                   {"position": "RB"}]},
        ]},
        "1": {"player": [
            [{"player_key": "461.p.200"}, {"name": {"full": "B Bench"}},
             {"display_position": "RB,W/R/T"}, {"status": "Q"},
             {"bye_weeks": {"week": "2"}}, {"is_editable": 0}],
            {"selected_position": [{"coverage_type": "week", "week": "2"},
                                   {"position": "BN"}]},
        ]},
        "count": 2}}}},
]}}


def _roster():
    api = _api()
    api._request = lambda path, **k: FIXTURE
    return api, api.read_roster()


def test_read_roster_normalizes_to_executor_shape():
    api, roster = _roster()
    assert api.current_week == 2
    a, b = roster
    assert a == {"name": "A Starter", "slot": "RB", "primary": "RB", "status": "",
                 "bye": False, "proj": 0.0, "locked": False, "player_key": "461.p.100"}
    assert b["slot"] == "BN" and b["status"] == "Q" and b["bye"] is True
    assert b["locked"] is True          # is_editable == 0
    assert b["primary"] == "RB"         # first token of display_position fallback


def test_read_roster_dedupes_repeated_player_entries():
    # Yahoo subresource queries can surface the same player node twice.
    doubled = {"a": FIXTURE, "b": FIXTURE}
    api = _api()
    api._request = lambda path, **k: doubled
    assert len(api.read_roster()) == 2      # not 4


# ---------- decide() -> API bridging ----------

def test_changes_for_swap_maps_names_to_keys():
    _, roster = _roster()
    changes = changes_for_swap("a starter", "B. Bench", "RB", roster)
    assert changes == [("461.p.200", "RB"), ("461.p.100", "BN")]


def test_changes_for_swap_unknown_name_raises():
    _, roster = _roster()
    with pytest.raises(KeyError):
        changes_for_swap("Nobody", "B Bench", "RB", roster)


# ---------- XML write body ----------

def test_build_roster_xml_shape_and_escaping():
    xml = YahooApi.build_roster_xml([("461.p.1", "W/R/T"), ("461.p.<2>", "BN")], 5).decode()
    assert xml.startswith('<?xml version="1.0"?><fantasy_content><roster>')
    assert "<coverage_type>week</coverage_type><week>5</week>" in xml
    assert "<player><player_key>461.p.1</player_key><position>W/R/T</position></player>" in xml
    assert "461.p.&lt;2&gt;" in xml     # escaped, never raw


# ---------- executor dispatcher (plan P2) ----------

def test_write_path_defaults_to_browser_in_template():
    # assert the TEMPLATE, not the user's live config.json state
    example = json.loads((ROOT / "config.example.json").read_text())
    assert example.get("write_path", "browser") == "browser"


def test_api_main_missing_secrets_exits_login_required(monkeypatch):
    import set_lineup as sl
    monkeypatch.setattr(sl, "notify", lambda *a, **k: None)   # no live pushes
    monkeypatch.setattr(yahoo_api, "DEFAULT_SECRETS_PATH",
                        pathlib.Path("/nonexistent/yahoo.json"))
    status_file = ROOT / "logs" / "last_status.json"
    backup = status_file.read_text() if status_file.exists() else None
    try:
        with pytest.raises(SystemExit) as e:
            sl.api_main()
        assert e.value.code == 2        # LOGIN_REQUIRED contract preserved
        status = json.loads(status_file.read_text())
        assert status["status"] == "LOGIN_REQUIRED"
        assert "yahoo_auth" in status["summary"]
    finally:                            # never leave test residue in logs/
        if backup is not None:
            status_file.write_text(backup)
        else:
            status_file.unlink(missing_ok=True)
