"""Connect Etsy (tools/etsy/connect.py): PKCE sign-in, single-use state, tokens saved to the vault, never shown.
Etsy is faked with httpx.MockTransport through the commerce helper."""
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

KEY, SEC = "keystr1ng", "s3cret"
ACCESS, REFRESH = "12345678.access-token-value", "12345678.refresh-token-value"


@pytest.fixture
def etsy(monkeypatch, tmp_path):
    from tools.commerce import _http as h
    from tools.etsy import _client, connect
    monkeypatch.setattr(connect, "GRANTED_FILE", tmp_path / "etsy_connection.json")
    monkeypatch.setattr(h, "enabled", lambda key: False)  # no listing write switch on: read-only scopes
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setenv("ETSY_KEYSTRING", KEY)
    monkeypatch.setenv("ETSY_SHARED_SECRET", SEC)
    saved, calls = {}, []
    import vault

    def fake_set(name, value, **kw):  # the real vault's checks on kind and used_by (a bad kind failed live, 2026-10-06)
        assert kw.get("kind", "token") in vault.KINDS, kw.get("kind")
        assert isinstance(kw.get("used_by", []), list)
        saved[name] = value
    monkeypatch.setattr(vault, "set_credential", fake_set)
    connect._pending.clear()
    _client.forget()
    shop = {"shop_id": 4242, "shop_name": "Shed Prints"}

    def handler(request):
        calls.append(request)
        if request.url.path == "/v3/public/oauth/token":
            form = parse_qs(request.content.decode())
            if form.get("code") == ["bad"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": f"bad code for {KEY}"})
            return httpx.Response(200, json={"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600})
        if request.url.path == "/v3/application/users/12345678/shops":
            return httpx.Response(200, json=shop)
        return httpx.Response(404, json={"error": "nope"})
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(handler))
    return connect, saved, calls, shop


def _state(url):
    return parse_qs(urlsplit(url).query)


def test_start_gives_an_s256_pkce_sign_in(etsy):
    connect, *_ = etsy
    r = connect.start("https://panel.example.com/api/etsy/callback")
    q = _state(r["url"])
    assert r["url"].startswith("https://www.etsy.com/oauth/connect?")
    assert q["client_id"] == [KEY] and q["code_challenge_method"] == ["S256"] and q["scope"] == ["shops_r listings_r transactions_r"]
    verifier = connect._pending[q["state"][0]]["verifier"]
    assert 43 <= len(verifier) <= 128
    assert q["code_challenge"][0] == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def test_listing_edits_are_asked_for_only_while_a_write_switch_is_on(etsy, monkeypatch):
    from tools.commerce import _http as h
    from tools.etsy import _client
    connect, saved, *_ = etsy
    monkeypatch.setenv("ETSY_REFRESH_TOKEN", "old-read-only-connection")
    assert connect.status()["reconnect_for_edits"] is False and connect.scopes() == "shops_r listings_r transactions_r"
    monkeypatch.setattr(h, "enabled", lambda key: key == _client.WRITE_SETTING)
    assert connect.status()["reconnect_for_edits"] is True  # connected before, read-only
    q = _state(connect.start("https://panel.example.com/api/etsy/callback")["url"])
    assert q["scope"] == ["shops_r listings_r transactions_r listings_w"]
    connect.finish("the-code", q["state"][0])
    assert connect.granted().endswith("listings_w") and connect.status()["reconnect_for_edits"] is False


@pytest.mark.parametrize("bad", ["http://panel.lan/api/etsy/callback", "panel.example.com/cb", "", "https://u:p@x.com/cb"])
def test_https_return_address_only(etsy, bad):
    connect, *_ = etsy
    with pytest.raises(Exception, match="https"):
        connect.start(bad)


def test_finish_saves_token_and_shop_and_shows_neither(etsy):
    connect, saved, calls, _ = etsy
    q = _state(connect.start("https://panel.example.com/api/etsy/callback")["url"])
    out = connect.finish("the-code", q["state"][0])
    assert out == {"connected": True, "shop_name": "Shed Prints", "shop_id_saved": True, "note": None}
    assert saved == {"ETSY_REFRESH_TOKEN": REFRESH, "ETSY_SHOP_ID": "4242"}
    token_call = parse_qs(calls[0].content.decode())
    assert token_call["grant_type"] == ["authorization_code"] and token_call["code_verifier"] and "client_secret" not in token_call
    assert calls[0].headers["x-api-key"] == f"{KEY}:{SEC}"
    assert REFRESH not in json.dumps(out) and ACCESS not in json.dumps(out)


def test_state_is_single_use_and_unknown_refused(etsy):
    connect, *_ = etsy
    s = _state(connect.start("https://panel.example.com/cb")["url"])["state"][0]
    connect.finish("c", s)
    with pytest.raises(Exception, match="already used"):
        connect.finish("c", s)
    with pytest.raises(Exception, match="already used"):
        connect.finish("c", "made-up")


def test_expired_sign_in(etsy, monkeypatch):
    connect, *_ = etsy
    s = _state(connect.start("https://panel.example.com/cb")["url"])["state"][0]
    connect._pending[s]["expires"] = 0
    with pytest.raises(Exception, match="10 minutes"):
        connect.finish("c", s)


def test_pasted_address_works_and_etsy_errors_hide_secrets(etsy):
    connect, saved, *_ = etsy
    s = _state(connect.start("https://example.com/etsy")["url"])["state"][0]
    assert connect.finish_url(f"https://example.com/etsy?code=the-code&state={s}")["connected"]
    s2 = _state(connect.start("https://example.com/etsy")["url"])["state"][0]
    with pytest.raises(Exception) as e:
        connect.finish_url(f"https://example.com/etsy?code=bad&state={s2}")
    assert "400" in str(e.value) and KEY not in str(e.value)
    with pytest.raises(Exception, match="no code"):
        connect.finish_url("https://example.com/etsy")
    with pytest.raises(Exception, match="Etsy said"):
        connect.finish_url("https://example.com/etsy?error=access_denied")


def test_pending_sign_ins_are_capped(etsy):
    connect, *_ = etsy
    for _ in range(connect.MAX_PENDING + 3):
        connect.start("https://panel.example.com/cb")
    assert len(connect._pending) == connect.MAX_PENDING


def test_missing_app_keys_are_named(etsy, monkeypatch):
    connect, *_ = etsy
    monkeypatch.delenv("ETSY_KEYSTRING")
    with pytest.raises(Exception, match="ETSY_KEYSTRING"):
        connect.start("https://panel.example.com/cb")


def test_status_is_names_and_yes_no(etsy):
    connect, *_ = etsy
    st = connect.status()
    assert st["app_ready"] is True and st["connected"] is False and st["names"]["shop_id"] == "ETSY_SHOP_ID"
    assert KEY not in json.dumps(st) and SEC not in json.dumps(st)


def test_status_fills_a_missing_shop_number(etsy, monkeypatch):
    connect, saved, _, _ = etsy
    monkeypatch.setenv("ETSY_REFRESH_TOKEN", REFRESH)
    st = connect.status()
    assert saved.get("ETSY_SHOP_ID") == "4242" and "saved the shop number" in st["note"]
