"""The Control Panel's release gates C7-C9 (2026-10-01): no default password and no open mode, CSRF on cookie
changes, signing out ends the session on the server, the lockout counts real addresses, and the owner can change
the password. Test list drafted by local_ai.ask (Qwen3-Coder-30B), checked and completed by hand."""
import base64
import logging

import pytest
from fastapi.testclient import TestClient

main = None  # imported by the fixture below: main.py reads its settings at import, after conftest sets them

SAVED = "correct-horse-battery-staple"  # secret-scan: allow (fake test password)
PANEL = {"Origin": "http://testserver"}  # a browser on the panel's own page


def basic(password):
    return {"Authorization": "Basic " + base64.b64encode(f"x:{password}".encode()).decode()}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    global main
    import main as dashboard_main
    main = dashboard_main
    for name, value in (("OWNER_FILE", tmp_path / "owner.json"), ("_owner_cache", {"key": None, "data": None}),
                        ("REVOKED_FILE", tmp_path / "revoked.json"), ("_revoked_cache", {"key": None, "data": {}}),
                        ("SESSION_SECRET_FILE", tmp_path / "session-secret"), ("_server_secret_cache", {"value": None}),
                        ("VIEWERS_FILE", tmp_path / "viewers.json"), ("_viewers_cache", {"sig": None, "data": None}),
                        ("PANEL_SETTINGS_FILE", tmp_path / "panel-settings.json"),
                        ("_panel_cache", {"sig": None, "data": {}}), ("_failures", {}), ("_all_failures", []),
                        ("DASHBOARD_PASSWORD", ""), ("DASHBOARD_VIEWER_PASSWORD", ""), ("DASHBOARD_PUBLIC_URL", ""),
                        ("TRUST_PROXY_HEADERS", False), ("VAULT_REVEAL_KEY", ""), ("TRUSTED_PROXIES", ""),
                        ("_trusted_nets", [])):
        monkeypatch.setattr(main, name, value)
    monkeypatch.delenv("DASHBOARD_SESSION_SECRET", raising=False)
    return tmp_path


def signed_in(password=SAVED):
    client = TestClient(main.app, headers=PANEL)
    assert client.post("/login", json={"password": password}).json()["role"] == "owner"
    return client


# --- C7: no default password, no open mode ---------------------------------------------------------------------

def test_first_start_makes_a_password_and_keeps_only_its_hash(fresh):
    made = main.first_run_password()
    assert made and len(made) >= 19 and made.count("-") == 3
    saved = (fresh / "owner.json").read_text(encoding="utf-8")
    assert made not in saved and all(k in saved for k in ("salt", "hash", "session_secret"))
    assert main.first_run_password() is None  # the next start keeps it and prints nothing
    assert main._owner_ok(made) and not main._owner_ok("guess")


def test_with_no_password_anywhere_nothing_is_open():
    client = TestClient(main.app)
    assert client.get("/api/me").status_code == 401
    page = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 302 and page.headers["location"].startswith("/login")


def test_an_unreadable_owner_file_is_never_overwritten_and_nobody_signs_in(fresh):
    (fresh / "owner.json").write_text("{not json", encoding="utf-8")
    assert main.first_run_password() is None
    assert (fresh / "owner.json").read_text(encoding="utf-8") == "{not json"
    assert TestClient(main.app).post("/login", json={"password": "anything"}).status_code == 401  # secret-scan: allow (fake)


def test_public_paths_stay_open():
    client = TestClient(main.app)
    assert client.get("/login").status_code == 200
    assert client.get("/fonts/none.woff2").status_code != 401
    assert client.get("/healthz").json() == {"status": "ok"}  # Docker's HEALTHCHECK: no password on a first run


def test_without_the_owners_server_list_the_models_come_from_homeshed(monkeypatch):
    """Gate A2: a public install has no private_panel.py. Its models are the ones HomeShed is set up with."""
    main._save_owner(SAVED)
    monkeypatch.setattr(main, "LOCAL_AI_SERVERS", [])
    monkeypatch.setattr(main, "_backends_cache", {"at": 0.0, "rows": []})

    async def backends(path):
        assert path == "/local-ai/backends"
        return 200, {"backends": [{"name": "my-ollama", "cloud": False, "configured": True, "benched": False},
                                  {"name": "cloud-x", "cloud": True, "configured": True, "benched": True},
                                  {"name": "unset", "cloud": True, "configured": False, "benched": False}]}

    monkeypatch.setattr(main, "_tool_json", backends)
    client = TestClient(main.app, headers=basic(SAVED))
    servers = client.get("/api/local-ai").json()["servers"]
    assert [(s["name"], s["online"], s["total_tokens"]) for s in servers] == [("my-ollama", True, None), ("cloud-x", False, None)]


# --- C9: CSRF --------------------------------------------------------------------------------------------------

def test_a_cookie_change_must_come_from_the_panels_own_page(monkeypatch):
    main._save_owner(SAVED)
    client = signed_in()
    cookie = {"cp_session": client.cookies.get("cp_session")}
    bare = TestClient(main.app, cookies=cookie)
    assert bare.post("/api/viewers", json={"name": "ann"}).status_code == 403  # no Origin at all
    for headers, ok in (({"Origin": "https://evil.example"}, False), ({"Origin": "null"}, False),
                        ({"Referer": "http://testserver/#settings"}, True), (PANEL, True),
                        ({"Origin": "https://panel.example", "X-Forwarded-Host": "panel.example"}, True)):
        r = bare.post("/api/viewers", json={"name": f"v{len(str(headers))}"}, headers=headers)
        assert (r.status_code == 200) is ok, (headers, r.status_code)
    monkeypatch.setattr(main, "DASHBOARD_PUBLIC_URL", "https://public.example")
    assert bare.post("/api/viewers", json={"name": "pub"}, headers={"Origin": "https://public.example"}).status_code == 200


def test_scripts_with_basic_auth_are_not_asked_for_an_origin():
    main._save_owner(SAVED)
    assert TestClient(main.app, headers=basic(SAVED)).post("/api/viewers", json={"name": "bob"}).status_code == 200


def test_signing_in_from_another_site_is_refused():
    main._save_owner(SAVED)
    client = TestClient(main.app)
    assert client.post("/login", json={"password": SAVED}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/login", json={"password": SAVED}).status_code == 200  # a script: no Origin


# --- C8: sessions and the lockout ------------------------------------------------------------------------------

def test_signing_out_ends_the_session_even_after_a_restart(monkeypatch):
    main._save_owner(SAVED)
    client = signed_in()
    token = client.cookies.get("cp_session")
    copy = TestClient(main.app, cookies={"cp_session": token})
    assert copy.get("/api/me").status_code == 200
    client.get("/logout", follow_redirects=False)
    assert copy.get("/api/me").status_code == 401
    monkeypatch.setattr(main, "_revoked_cache", {"key": None, "data": {}})  # a restart: nothing in memory
    assert copy.get("/api/me").status_code == 401


def test_forwarded_addresses_count_only_behind_a_trusted_proxy(monkeypatch):
    main._save_owner(SAVED)
    client = TestClient(main.app)
    for i in range(5):  # a guesser naming a new address each time is still one address
        client.post("/login", json={"password": "guess"}, headers={"cf-connecting-ip": f"10.0.0.{i}"})
    assert client.post("/login", json={"password": "guess"}, headers={"cf-connecting-ip": "10.0.0.99"}).status_code == 429
    monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(main, "_proxy_trusted", lambda peer: True)  # the request came through the tunnel
    monkeypatch.setattr(main, "_failures", {})
    monkeypatch.setattr(main, "_all_failures", [])
    for i in range(5):
        client.post("/login", json={"password": "guess"}, headers={"cf-connecting-ip": "10.0.1.1"})
    assert client.post("/login", json={"password": SAVED}, headers={"cf-connecting-ip": "10.0.1.2"}).status_code == 200


def test_only_a_trusted_proxy_may_name_the_address(monkeypatch):
    """R&D's security review, 2026-10-01: with TRUST_PROXY_HEADERS on, any client could claim a new address each try and
    never be locked out. Only this machine, the container's gateway (where a tunnel on the same host arrives) or
    TRUSTED_PROXIES may now; anyone else's header is ignored."""
    main._save_owner(SAVED)
    monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
    client = TestClient(main.app)  # its peer is "testclient": not a trusted proxy
    for i in range(5):
        client.post("/login", json={"password": "guess"}, headers={"cf-connecting-ip": f"10.0.2.{i}"})
    assert client.post("/login", json={"password": SAVED}, headers={"cf-connecting-ip": "10.0.2.99"}).status_code == 429
    for given, peer, trusted in (("", "127.0.0.1", True), ("", "192.168.1.103", False),
                                 ("172.30.0.0/16, 10.9.9.9", "172.30.0.2", True), ("172.30.0.0/16", "127.0.0.1", False),
                                 ("not-an-address", "127.0.0.1", False)):
        monkeypatch.setattr(main, "TRUSTED_PROXIES", given)
        monkeypatch.setattr(main, "_trusted_nets", [])
        monkeypatch.setattr(main, "_gateway", lambda: None)
        assert main._proxy_trusted(peer) is trusted, (given, peer)
    monkeypatch.setattr(main, "TRUSTED_PROXIES", "")
    monkeypatch.setattr(main, "_trusted_nets", [])
    monkeypatch.setattr(main, "_gateway", lambda: "172.18.0.1")
    assert main._proxy_trusted("172.18.0.1") and not main._proxy_trusted("172.18.0.7")


def test_the_global_cap_stops_a_guesser_spread_over_many_addresses(monkeypatch):
    main._save_owner(SAVED)
    monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(main, "_proxy_trusted", lambda peer: True)  # the request came through the tunnel
    client = TestClient(main.app)
    for i in range(main.GLOBAL_MAX_FAILURES):
        client.post("/login", json={"password": "guess"}, headers={"cf-connecting-ip": f"10.1.{i // 250}.{i % 250}"})
    assert client.post("/login", json={"password": SAVED}, headers={"cf-connecting-ip": "10.9.9.9"}).status_code == 429


def test_a_wrong_password_is_logged_without_the_password(caplog):
    main._save_owner(SAVED)
    with caplog.at_level(logging.WARNING, logger="dashboard"):
        TestClient(main.app).post("/login", json={"password": "my-guess-123"})  # secret-scan: allow (fake)
    assert "wrong password from" in caplog.text and "my-guess-123" not in caplog.text


# --- the owner's own password ----------------------------------------------------------------------------------

def test_changing_the_password(monkeypatch):
    main._save_owner(SAVED)
    client, other = signed_in(), signed_in()
    r = client.post("/api/owner/password", json={"current": "not-it", "new": "a-brand-new-password"})
    assert r.status_code == 403 and len(main._failures["testclient"]) == 1  # counts toward the lockout
    assert client.post("/api/owner/password", json={"current": SAVED, "new": "short"}).status_code == 400
    assert client.post("/api/owner/password", json={"current": SAVED, "new": "a-brand-new-password"}).status_code == 200
    assert client.get("/api/me").status_code == 200    # this browser stays signed in
    assert other.get("/api/me").status_code == 401     # every other session ends
    assert main._owner_ok("a-brand-new-password") and not main._owner_ok(SAVED)
    assert client.get("/api/owner").json()["in_env"] is False


def test_a_password_set_in_the_env_is_changed_there(monkeypatch):
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "env-password-123")
    client = TestClient(main.app, headers=basic("env-password-123"))
    r = client.post("/api/owner/password", json={"current": "env-password-123", "new": "a-brand-new-password"})
    assert r.status_code == 409 and ".env" in r.json()["error"]
    assert client.get("/api/owner").json()["in_env"] is True


def test_revealing_a_value_rechecks_the_saved_password():
    main._save_owner(SAVED)
    client = TestClient(main.app, headers=basic(SAVED))
    assert client.post("/api/vault/X_KEY/reveal", json={"password": "wrong"}).status_code == 403
    assert client.post("/api/vault/X_KEY/reveal", json={"password": SAVED}).status_code == 503  # right: reveal not set up
