"""Sign in with OAuth (oauth_server.py + server.py's sign-in routes), end to end through the real request gate: a chat
app registers (or names itself by URL, CIMD), the owner approves on the Control Panel, the app gets tokens that map to
an app on the API page, refreshes rotate, and deleting the app ends it all. Files go under tmp_path."""
import base64
import hashlib
import importlib
import secrets
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

import clients
import oauth_server

BASE = "https://mcp.example.test"
_real_tell_owner = oauth_server._tell_owner
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(clients, "CLIENTS_FILE", tmp_path / "clients.json")
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(clients, "_cache", (None, {"clients": {}}))
    for name in ("_windows", "_last_seen", "_touched", "_connections"):
        monkeypatch.setattr(clients, name, {})
    monkeypatch.setattr(oauth_server, "STORE_FILE", tmp_path / "oauth.json")
    monkeypatch.setattr(oauth_server, "_cimd_cache", {})
    monkeypatch.setattr(oauth_server, "_tell_owner", lambda item: None)
    monkeypatch.setenv("MCP_PUBLIC_URL", BASE)
    return tmp_path


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "testserver")
    sys.modules.pop("server", None)
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "AUTH_TOKEN", "owner-secret")
    monkeypatch.setattr(server, "ALLOWED_HOSTS", ["testserver"])
    return TestClient(server.http_app(), base_url="http://testserver", follow_redirects=False)


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register(http):
    r = http.post("/register", json={"redirect_uris": [CALLBACK], "client_name": "Claude",
                                     "token_endpoint_auth_method": "none",
                                     "grant_types": ["authorization_code", "refresh_token"],
                                     "response_types": ["code"]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _authorize(http, client_id, challenge, state="s1"):
    r = http.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                                       "code_challenge": challenge, "code_challenge_method": "S256",
                                       "state": state, "resource": BASE + "/mcp"})
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert loc.startswith(BASE + "/oauth/consent/")
    return loc.rsplit("/", 1)[1]


def _sign_in(http, preset="read"):
    client_id = _register(http)
    verifier, challenge = _pkce()
    pid = _authorize(http, client_id, challenge)
    assert http.get(f"/oauth/consent/{pid}/status").json()["status"] == "waiting"
    [row] = oauth_server.pending()
    assert row["app_name"] == "Claude" and len(row["match"]) == 6 and row["redirect_host"] == "claude.ai"
    out = oauth_server.decide(pid, True, preset=preset)
    status = http.get(f"/oauth/consent/{pid}/status").json()
    q = parse_qs(urlsplit(status["redirect"]).query)
    assert status["status"] == "approved" and q["state"] == ["s1"]
    assert "redirect" not in http.get(f"/oauth/consent/{pid}/status").json()  # handed out once
    tok = http.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "client_id": client_id,
                                    "redirect_uri": CALLBACK, "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    return out["app"], client_id, tok.json(), q["code"][0], verifier


def test_discovery_says_where_and_how_to_sign_in(http):
    r = http.post("/mcp", json={})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == \
        f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"'
    prm = http.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == BASE + "/mcp" and prm["authorization_servers"] == [BASE + "/"]
    meta = http.get("/.well-known/oauth-authorization-server").json()
    assert meta["authorization_endpoint"] == BASE + "/authorize" and meta["registration_endpoint"] == BASE + "/register"
    assert "none" in meta["token_endpoint_auth_methods_supported"] and meta["client_id_metadata_document_supported"]
    assert meta["code_challenge_methods_supported"] == ["S256"]


def test_a_full_sign_in_gives_tokens_that_act_as_the_app(http):
    app, client_id, tok, code, verifier = _sign_in(http)
    assert tok["access_token"].startswith("hso_") and tok["expires_in"] == oauth_server.ACCESS_TTL_S
    assert clients.resolve("Bearer " + tok["access_token"]) == (app, "ok")
    listed = {c["name"]: c for c in clients.list_clients()["clients"]}
    assert listed[app]["tool_view"] == "front_door" and listed[app]["allow"] == ["read:*"]
    assert http.get("/clients", headers={"Authorization": "Bearer " + tok["access_token"]}).status_code == 403
    again = http.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": client_id,
                                      "redirect_uri": CALLBACK, "code_verifier": verifier})
    assert again.status_code == 400  # a code works once


def test_the_wrong_pkce_verifier_gets_nothing(http):
    client_id = _register(http)
    _, challenge = _pkce()
    pid = _authorize(http, client_id, challenge)
    oauth_server.decide(pid, True)
    code = parse_qs(urlsplit(http.get(f"/oauth/consent/{pid}/status").json()["redirect"]).query)["code"][0]
    r = http.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": client_id,
                                  "redirect_uri": CALLBACK, "code_verifier": secrets.token_urlsafe(48)})
    assert r.status_code == 400


def test_refresh_rotates_and_the_old_refresh_token_stops(http):
    app, client_id, tok, *_ = _sign_in(http)
    r = http.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                  "client_id": client_id})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["refresh_token"] != tok["refresh_token"] and clients.resolve("Bearer " + new["access_token"])[1] == "ok"
    old = http.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                    "client_id": client_id})
    assert old.status_code == 400


def test_deleting_or_suspending_the_app_ends_the_sign_in(http):
    app, client_id, tok, *_ = _sign_in(http)
    clients.set_suspended(app, True)
    assert clients.resolve("Bearer " + tok["access_token"]) == (app, "suspended")
    clients.set_suspended(app, False)
    clients.delete(app)
    assert clients.resolve("Bearer " + tok["access_token"]) == (None, "unknown")
    clients.create(app)  # a new app with the same name never inherits the old sign-in
    assert clients.resolve("Bearer " + tok["access_token"]) == (None, "unknown")
    r = http.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                  "client_id": client_id})
    assert r.status_code == 400


def test_declining_sends_the_browser_back_with_access_denied(http):
    client_id = _register(http)
    _, challenge = _pkce()
    pid = _authorize(http, client_id, challenge, state="xyz")
    assert oauth_server.decide(pid, False)["status"] == "declined"
    q = parse_qs(urlsplit(http.get(f"/oauth/consent/{pid}/status").json()["redirect"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["xyz"]
    assert clients.list_clients()["clients"] == []
    with pytest.raises(oauth_server.OAuthError):
        oauth_server.decide(pid, True)


def test_approving_as_an_existing_app_keeps_its_grants(http):
    clients.create("my-claude", preset="all")
    client_id = _register(http)
    verifier, challenge = _pkce()
    pid = _authorize(http, client_id, challenge)
    assert oauth_server.decide(pid, True, app="my-claude", preset="none")["app"] == "my-claude"
    assert {c["name"]: c for c in clients.list_clients()["clients"]}["my-claude"]["allow"] == ["*"]


def test_owner_routes_need_the_owner(http):
    client_id = _register(http)
    _, challenge = _pkce()
    pid = _authorize(http, client_id, challenge)
    owner = {"Authorization": "Bearer owner-secret"}
    assert http.get("/oauth/pending").status_code == 401
    listed = http.get("/oauth/pending", headers=owner).json()
    assert listed["enabled"] and listed["pending"][0]["id"] == pid
    r = http.post(f"/oauth/pending/{pid}/approve", json={"preset": "none"}, headers=owner)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert http.post(f"/oauth/pending/{pid}/approve", json={}, headers=owner).status_code == 400


def test_bad_redirects_and_unknown_resources_are_refused(http):
    r = http.post("/register", json={"redirect_uris": ["http://evil.example/cb"], "token_endpoint_auth_method": "none"})
    assert r.status_code == 400
    client_id = _register(http)
    _, challenge = _pkce()
    r = http.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                                       "code_challenge": challenge, "code_challenge_method": "S256", "state": "s",
                                       "resource": "https://other.example/mcp"})
    assert r.status_code == 302 and "invalid_target" in r.headers["location"]


def test_cimd_reads_a_published_identity_and_checks_it(monkeypatch):
    import netguard
    url = "https://claude.ai/oauth/claude-code-client-metadata"
    good = {"client_id": url, "client_name": "Claude", "redirect_uris": [CALLBACK]}
    docs = {}

    peer = {"ip": "160.79.104.10"}

    class Stream:
        def get_extra_info(self, name):
            return (peer["ip"], 443) if name == "server_addr" else None

    class Resp:
        def __init__(self, body, status=200):
            self.body, self.status_code = body, status
            self.extensions = {"network_stream": Stream()}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self):
            yield self.body

    class Http:
        def __init__(self, **kw):
            assert kw["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, u, headers=None):
            import json
            return Resp(json.dumps(docs[u]).encode())

    import httpx
    monkeypatch.setattr(httpx, "Client", Http)
    monkeypatch.setattr(netguard, "resolve_permitted", lambda host: ["160.79.104.10"])
    docs[url] = good
    assert oauth_server.fetch_cimd(url)["client_name"] == "Claude"
    oauth_server._cimd_cache.clear()
    docs[url] = {**good, "client_id": "https://someone-else.example/x"}
    assert oauth_server.fetch_cimd(url) is None
    docs[url] = {**good, "redirect_uris": ["http://evil.example/cb"]}
    assert oauth_server.fetch_cimd(url) is None
    assert oauth_server.fetch_cimd("http://claude.ai/x") is None and oauth_server.fetch_cimd("https://10.0.0.5/x") is None
    docs[url] = good
    oauth_server._cimd_cache.clear()
    peer["ip"] = "192.168.1.20"  # the name checked out, then answered from inside the network: DNS rebinding
    assert oauth_server.fetch_cimd(url) is None

    def blocked(host):
        raise netguard.BlockedHost("internal")
    monkeypatch.setattr(netguard, "resolve_permitted", blocked)
    assert oauth_server.fetch_cimd("https://intranet.example/x") is None


def test_sign_in_is_off_without_a_public_url(monkeypatch, http):
    monkeypatch.delenv("MCP_PUBLIC_URL")
    sys.modules.pop("server", None)
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "AUTH_TOKEN", "owner-secret")
    monkeypatch.setattr(server, "ALLOWED_HOSTS", ["testserver"])
    plain = TestClient(server.http_app(), base_url="http://testserver", follow_redirects=False)
    assert plain.get("/.well-known/oauth-authorization-server").status_code == 401
    assert "www-authenticate" not in plain.post("/mcp", json={}).headers


def test_a_reused_refresh_token_ends_the_whole_sign_in(http):
    app, client_id, tok, *_ = _sign_in(http)
    first = http.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                      "client_id": client_id}).json()
    assert clients.resolve("Bearer " + first["access_token"])[1] == "ok"
    stolen = http.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                       "client_id": client_id})
    assert stolen.status_code == 400
    assert clients.resolve("Bearer " + first["access_token"]) == (None, "unknown")  # the thief's copy and ours
    again = http.post("/token", data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"],
                                      "client_id": client_id})
    assert again.status_code == 400


def test_revoking_ends_older_tokens_of_the_same_sign_in(http):
    app, client_id, tok, *_ = _sign_in(http)
    new = http.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                    "client_id": client_id}).json()
    assert clients.resolve("Bearer " + tok["access_token"])[1] == "ok"  # the first access token still runs
    import asyncio
    provider = oauth_server.Provider()  # the SDK's /revoke wants a client secret, which a chat app doesn't have
    asyncio.run(provider.revoke_token(asyncio.run(provider.load_access_token(new["access_token"]))))
    assert clients.resolve("Bearer " + tok["access_token"]) == (None, "unknown")


def test_sign_in_steps_are_rate_limited_per_address(http, monkeypatch):
    guard = http.app
    monkeypatch.setattr(type(guard), "OAUTH_MAX", 3)
    codes = [http.post("/register", json={"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
                       ).status_code for _ in range(4)]
    assert codes[:3] == [201] * 3 and codes[3] == 429
    assert http.get("/oauth/consent/nope/status").status_code == 200  # the waiting page's polls aren't counted


def test_owner_alerts_are_capped(monkeypatch):
    sent = []
    import printify_pending
    monkeypatch.setattr(printify_pending, "_note", lambda *a: sent.append(a))
    monkeypatch.setattr(oauth_server, "_alerts", [])
    monkeypatch.setattr(oauth_server, "_tell_owner", oauth_server.__dict__["_tell_owner"].__wrapped__
                        if hasattr(oauth_server._tell_owner, "__wrapped__") else _real_tell_owner)
    for _ in range(oauth_server.ALERTS_PER_HOUR + 5):
        oauth_server._tell_owner({"client_name": "x", "match": "123456"})
    assert len(sent) == oauth_server.ALERTS_PER_HOUR


def test_pending_says_whether_the_app_is_verified(http):
    client_id = _register(http)
    _, challenge = _pkce()
    _authorize(http, client_id, challenge)
    [row] = oauth_server.pending()
    assert row["verified"] is False and row["client_host"] is None


def _trusted_sign_in(http):
    """A sign-in the owner approves with trust ticked; returns (app, client_id, cookie name, cookie value)."""
    client_id = _register(http)
    _, challenge = _pkce()
    pid = _authorize(http, client_id, challenge)
    out = oauth_server.decide(pid, True, trust=True)
    assert out["trusted"]
    on_disk = oauth_server.STORE_FILE.read_text(encoding="utf-8")
    hand = oauth_server._handoff[pid]
    assert hand["cookie"] not in on_disk and hand["redirect"] not in on_disk  # raw secrets live in memory only
    r = http.get(f"/oauth/consent/{pid}/status")
    assert "_trust_cookie" not in r.json() and "redirect" in r.json()  # the cookie is never in the page's JSON
    name = oauth_server.trust_cookie_name(client_id)
    set_cookie = r.headers["set-cookie"]
    assert set_cookie.startswith(name + "=") and "HttpOnly" in set_cookie and "Secure" in set_cookie
    assert "Path=/oauth/consent" in set_cookie
    return out["app"], client_id, name, set_cookie.split(";", 1)[0].split("=", 1)[1]


def test_a_trusted_browser_signs_the_same_app_in_without_asking(http, monkeypatch):
    told = []
    monkeypatch.setattr(oauth_server, "_tell_owner", lambda item: told.append(item))
    app, client_id, name, value = _trusted_sign_in(http)
    _, challenge = _pkce()
    pid = _authorize(http, client_id, challenge, state="s2")
    page = http.get(f"/oauth/consent/{pid}", headers={"Cookie": f"{name}={value}"})
    assert page.status_code == 200
    status = http.get(f"/oauth/consent/{pid}/status").json()
    assert status["status"] == "approved" and parse_qs(urlsplit(status["redirect"]).query)["state"] == ["s2"]
    assert told == []  # the trusted browser never alerts the owner
    assert [r["app"] for r in oauth_server.trusted()] == [app]


def test_trust_needs_the_browser_not_just_the_app(http, monkeypatch):
    told = []
    monkeypatch.setattr(oauth_server, "_tell_owner", lambda item: told.append(item))
    app, client_id, name, value = _trusted_sign_in(http)
    _, challenge = _pkce()
    pid = _authorize(http, client_id, challenge, state="s3")  # same app, a browser with no cookie
    http.get(f"/oauth/consent/{pid}")
    assert http.get(f"/oauth/consent/{pid}/status").json()["status"] == "waiting" and len(told) == 1
    pid2 = _authorize(http, client_id, challenge, state="s4")  # a made-up cookie
    http.get(f"/oauth/consent/{pid2}", headers={"Cookie": f"{name}=not-the-real-one"})
    assert http.get(f"/oauth/consent/{pid2}/status").json()["status"] == "waiting"
    other = _register(http)  # the real cookie, but for a different app
    pid3 = _authorize(http, other, challenge, state="s5")
    http.get(f"/oauth/consent/{pid3}", headers={"Cookie": f"{oauth_server.trust_cookie_name(other)}={value}"})
    assert http.get(f"/oauth/consent/{pid3}/status").json()["status"] == "waiting"


def test_removing_trust_or_the_app_asks_the_owner_again(http):
    app, client_id, name, value = _trusted_sign_in(http)
    [row] = oauth_server.trusted()
    headers = {"Authorization": "Bearer owner-secret"}
    assert http.post(f"/oauth/trusted/{row['id']}/remove", headers=headers).status_code == 200
    assert oauth_server.trusted() == []
    assert http.post(f"/oauth/trusted/{row['id']}/remove", headers=headers).status_code == 404
    _, challenge = _pkce()
    pid = _authorize(http, client_id, challenge, state="s6")
    http.get(f"/oauth/consent/{pid}", headers={"Cookie": f"{name}={value}"})
    assert http.get(f"/oauth/consent/{pid}/status").json()["status"] == "waiting"
    oauth_server.decide(pid, True, app=app, trust=True)
    assert len(oauth_server.trusted()) == 1
    oauth_server.end_app(app)  # deleting the app (or rotating its key) forgets its trusted browsers too
    assert oauth_server.trusted() == []
    assert http.get("/oauth/trusted").status_code in (401, 403)  # the owner's list needs the owner
