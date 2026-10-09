"""mavis_pro + pro_routes: HomeShed Pro in the public package (2026-10-01). The owner pastes a Pro key made on the Pro
website; it's checked with the site and kept only if the site knows it, in the vault. No password is ever typed into
HomeShed (R&D's security review: a fork asking for one could be phishing). Status follows whoami; disconnect forgets the
key. The Pro site is faked with httpx.MockTransport; its answers are the Pro backend's real ones (200 Pro, 403 a valid
key whose membership has ended, 401 unknown or revoked). Missing cases suggested by the local model (qwen3-coder-30b):
key length boundaries, a corrupted state file."""
import json

import httpx
import pytest
from cryptography.fernet import Fernet

import mavis_pro
import vault

KEY = "mav_" + "Qx7" * 10   # the shape of a real key, built at run time: no literal secret in this file


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault.json")
    monkeypatch.setattr(vault, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(vault, "_cache", (None, None))
    monkeypatch.delenv("MAVIS_PRO_TOKEN", raising=False)
    monkeypatch.setattr(mavis_pro, "STATE_FILE", str(tmp_path / "mavis_pro.json"))
    monkeypatch.setattr(mavis_pro.runtime_settings, "address", lambda name: "https://pro.test")
    mavis_pro._cache.clear()
    return tmp_path


def backend(monkeypatch, *, whoami=200, client=None, calls=None, down=False):
    """A fake Pro site for every httpx call the module makes."""
    calls = [] if calls is None else calls

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.headers.get("authorization")))
        if down:
            raise httpx.ConnectError("no route to host")
        if req.url.path == "/api/pro-api/whoami":
            return httpx.Response(whoami, json={"client": client if client is not None else {"name": "My laptop"},
                                                "scopes": []})
        if req.url.path == "/api/pro-api/packs":
            return httpx.Response(whoami, json={"data": [{"slug": "safe-operator", "title": "Safe Operator",
                                                          "description": "Stops costly habits", "price": 1}]})
        if req.url.path == "/api/pro-api/packs/huge":  # more than a pack can be
            return httpx.Response(200, content=b"x" * (mavis_pro.PRO_MAX_BYTES + 1))
        if req.url.path.startswith("/api/pro-api/packs/") and not req.url.path.endswith("/missing"):
            slug = req.url.path.rsplit("/", 1)[-1]
            return httpx.Response(whoami, json={"data": {"content": json.dumps({"slug": slug, "title": "Pack"})}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(mavis_pro.httpx, "get", lambda url, **k: real_client(transport=transport).get(url, **k))
    monkeypatch.setattr(mavis_pro.httpx, "stream",
                        lambda method, url, **k: real_client(transport=transport).stream(method, url, **k))
    return calls


def test_an_answer_bigger_than_a_pack_is_refused_as_it_arrives(monkeypatch):
    """R&D's security review, 2026-10-01: the size was checked only after the whole body was read."""
    backend(monkeypatch)
    monkeypatch.setattr(mavis_pro.vault, "secret", lambda name, default=None: "mav_key")  # secret-scan: allow (fake)
    with pytest.raises(mavis_pro.MavisProError, match="more than a pack can be"):
        mavis_pro.pack("huge")


# --- connecting with a pasted key ---------------------------------------------------------------------------------
def test_a_pasted_key_is_checked_with_the_site_then_kept_in_the_vault_only(setup, monkeypatch):
    calls = backend(monkeypatch)
    out = mavis_pro.connect_key(f"  {KEY}\n")  # pasted with stray spaces
    assert out["connected"] and out["pro"] and out["client_name"] == "My laptop"
    assert vault.secret("MAVIS_PRO_TOKEN") == KEY
    on_disk = "".join(p.read_text(encoding="utf-8") for p in setup.iterdir() if p.is_file())
    assert KEY not in on_disk  # the vault encrypts it; the state file holds only the key's name
    assert calls == [("GET", "/api/pro-api/whoami", f"Bearer {KEY}")] * 2  # the check, then the fresh status


def test_a_key_whose_membership_has_ended_is_kept_and_says_so(monkeypatch):
    backend(monkeypatch, whoami=403)
    out = mavis_pro.connect_key(KEY)
    assert out["connected"] and not out["pro"] and "ended" in out["reason"]
    assert vault.secret("MAVIS_PRO_TOKEN") == KEY  # renewing on the website brings Pro back without a new key


def test_a_new_key_replaces_the_old_one(monkeypatch):
    backend(monkeypatch)
    mavis_pro.connect_key(KEY)
    newer = "mav_" + "Zz9" * 10
    mavis_pro.connect_key(newer)
    assert vault.secret("MAVIS_PRO_TOKEN") == newer


@pytest.mark.parametrize("whoami, down, words", [
    (401, False, "doesn't know that key"),    # mistyped or revoked
    (500, False, "couldn't check the key"),
    (200, True, "Couldn't reach"),
])
def test_a_key_the_site_doesnt_accept_is_refused_and_never_echoed(monkeypatch, whoami, down, words):
    backend(monkeypatch, whoami=whoami, down=down)
    with pytest.raises(mavis_pro.MavisProError) as err:
        mavis_pro.connect_key(KEY)
    assert words in str(err.value) and KEY not in str(err.value)
    assert vault.secret("MAVIS_PRO_TOKEN") is None


@pytest.mark.parametrize("bad", ["", "   ", "mav_" + "a" * 19, "QX7" * 10, "mav_" + "a" * 201, "mav_" + "a b" * 10,
                                 "Bearer " + KEY, KEY + ";rm", None])
def test_anything_that_isnt_a_key_is_refused_before_anything_is_sent(monkeypatch, bad):
    calls = backend(monkeypatch)
    with pytest.raises(mavis_pro.MavisProError, match="isn't a Pro key"):
        mavis_pro.connect_key(bad)
    assert calls == [] and vault.secret("MAVIS_PRO_TOKEN") is None


@pytest.mark.parametrize("length", [20, 200])
def test_the_shortest_and_longest_keys_are_accepted(monkeypatch, length):
    backend(monkeypatch)
    assert mavis_pro.connect_key("mav_" + "a" * length)["connected"]


@pytest.mark.parametrize("client, name", [({"name": "Desk PC"}, "Desk PC"), ("Old style name", "Old style name"),
                                          ({}, "Pro key"), ("x" * 300, "x" * 80)])
def test_the_key_is_remembered_by_its_name_on_the_site(monkeypatch, client, name):
    backend(monkeypatch, client=client)
    assert mavis_pro.connect_key(KEY)["client_name"] == name


def test_no_password_route_or_function_is_left():
    """R&D's security review, 2026-10-01: a public HomeShed must never ask for the Pro password."""
    assert not hasattr(mavis_pro, "connect") and not hasattr(mavis_pro, "_safe_for_password")


# --- status, packs, disconnect ------------------------------------------------------------------------------------
@pytest.mark.parametrize("code, connected, pro, words", [(200, True, True, None), (403, True, False, "ended"),
                                                         (401, False, False, "revoked"), (503, True, False, "HTTP 503")])
def test_status_follows_whoami(monkeypatch, code, connected, pro, words):
    vault.set_credential("MAVIS_PRO_TOKEN", KEY, kind="api-key")
    backend(monkeypatch, whoami=code)
    out = mavis_pro.status()
    assert (out["connected"], out["pro"]) == (connected, pro)
    assert words is None or words in out["reason"]
    assert out.get("unreachable", False) is (code == 503)  # only a non-answer is a blip pro.check rides out


def test_a_corrupted_state_file_doesnt_stop_the_status(setup, monkeypatch):
    vault.set_credential("MAVIS_PRO_TOKEN", KEY, kind="api-key")
    (setup / "mavis_pro.json").write_text("{not json", encoding="utf-8")
    backend(monkeypatch)
    out = mavis_pro.status()
    assert out["connected"] and out["pro"] and out["client_name"] is None


def test_status_without_a_key_asks_nobody(monkeypatch):
    """The no-call-home gate: nothing goes to the Pro site before the owner connects."""
    calls = backend(monkeypatch)
    assert mavis_pro.status() == {"connected": False, "pro": False, "address": "https://pro.test"}
    assert mavis_pro.packs() == {"configured": False, "available": [], "error": None}
    with pytest.raises(mavis_pro.MavisProError, match="isn't connected"):
        mavis_pro.pack("safe-operator")
    assert calls == []


def test_status_is_cached_for_ten_minutes(monkeypatch):
    vault.set_credential("MAVIS_PRO_TOKEN", KEY, kind="api-key")
    calls = backend(monkeypatch)
    mavis_pro.status()
    mavis_pro.status()
    assert len(calls) == 1
    mavis_pro.status(fresh=True)
    assert len(calls) == 2


def test_packs_come_with_this_installs_key(monkeypatch):
    """R&D's Control Panel review #9: installing a Pro pack needs no .env edit on the PC."""
    vault.set_credential("MAVIS_PRO_TOKEN", KEY, kind="api-key")
    calls = backend(monkeypatch)
    assert mavis_pro.packs()["available"] == [{"slug": "safe-operator", "title": "Safe Operator",
                                               "description": "Stops costly habits"}]
    assert mavis_pro.pack("safe-operator") == {"slug": "safe-operator", "title": "Pack"}
    assert all(c[2] == f"Bearer {KEY}" for c in calls)
    for slug, words in (("missing", "no pack"), ("../etc", "Unknown pack"), ("", "Unknown pack")):
        with pytest.raises(mavis_pro.MavisProError, match=words):
            mavis_pro.pack(slug)


def test_packs_say_when_pro_refuses(monkeypatch):
    vault.set_credential("MAVIS_PRO_TOKEN", KEY, kind="api-key")
    backend(monkeypatch, whoami=403)
    out = mavis_pro.packs()
    assert out["configured"] and out["available"] == [] and "refused" in out["error"]


def test_disconnect_forgets_the_key_and_names_it_for_revoking_on_the_site(setup, monkeypatch):
    backend(monkeypatch)
    mavis_pro.connect_key(KEY)
    out = mavis_pro.disconnect()
    assert out["connected"] is False and '"My laptop"' in out["note"] and "Access keys" in out["note"]
    assert vault.secret("MAVIS_PRO_TOKEN") is None and not (setup / "mavis_pro.json").exists()
    assert mavis_pro.disconnect() == {"connected": False, "note": None}  # twice is fine


# --- the tool server's routes (public; owner token only, like every route but /mcp) --------------------------------
class Req:
    def __init__(self, body=None, query=None, bad_json=False, slug=None):
        self._body, self._bad = body, bad_json
        self.query_params = query or {}
        self.path_params = {"slug": slug} if slug is not None else {}

    async def json(self):
        if self._bad:
            raise ValueError("Expecting value: line 1 column 1")
        return self._body


@pytest.fixture
def routes(monkeypatch):
    import pro_routes

    async def body(request):
        return await request.json()
    monkeypatch.setattr(pro_routes, "_body", body)
    return pro_routes


def call(handler, req):
    import asyncio
    resp = asyncio.run(handler(req))
    return resp.status_code, json.loads(resp.body)


def test_the_server_registers_the_public_pro_routes_and_no_password_route(monkeypatch):
    import importlib
    import sys
    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")  # secret-scan: allow (fake)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    server = importlib.import_module("server")
    got = {(r.path, m) for r in server.mcp._custom_starlette_routes for m in (r.methods or ()) if m != "HEAD"}
    assert {("/pro", "GET"), ("/pro", "DELETE"), ("/pro/connect", "POST"), ("/pro/packs", "GET"),
            ("/pro/packs/{slug}", "GET"), ("/pro/bugs/sync", "POST")} <= got
    assert not any(path.startswith("/mavis-pro") for path, _ in got)


@pytest.mark.parametrize("grace, badge", [(True, True), (False, False)])
def test_the_status_route_says_what_pro_features_actually_get(routes, monkeypatch, grace, badge):
    """KB-0052's front half: while the Pro site can't be reached, a recent membership still counts, and the badge
    must show that instead of Free."""
    monkeypatch.setattr(routes.mavis_pro, "status",
                        lambda fresh=False: {"connected": True, "pro": False, "unreachable": True})
    monkeypatch.setattr(routes.pro, "check", lambda: (grace, ""))
    status, body = call(routes.status_route, Req())
    assert status == 200 and body["pro"] is False and body["effective_pro"] is badge


def test_the_connect_route_passes_only_the_key_and_never_echoes_it(routes, monkeypatch):
    seen = []

    def refuse(key):
        seen.append(key)
        raise mavis_pro.MavisProError("The Pro website doesn't know that key: it was mistyped, or revoked.")
    monkeypatch.setattr(routes.mavis_pro, "connect_key", refuse)
    status, body = call(routes.connect_route, Req({"key": KEY, "email": "x@example.com", "password": "nope"}))
    assert status == 400 and "doesn't know that key" in body["error"] and KEY not in json.dumps(body)
    assert seen == [KEY]
    assert call(routes.connect_route, Req(bad_json=True)) == (400, {"error": 'Send the key as JSON: {"key": "mav_..."}.'})
    monkeypatch.setattr(routes.mavis_pro, "connect_key", lambda key: {"connected": True, "pro": True})
    assert call(routes.connect_route, Req({"key": KEY})) == (200, {"connected": True, "pro": True})


def test_status_and_disconnect_routes(routes, monkeypatch):
    monkeypatch.setattr(routes.mavis_pro, "status", lambda fresh=False: {"connected": True, "pro": True, "fresh": fresh})
    monkeypatch.setattr(routes.mavis_pro, "disconnect", lambda: {"connected": False, "note": None})
    monkeypatch.setattr(routes.pro, "check", lambda: (True, ""))  # the real one would save its grace file
    assert call(routes.status_route, Req(query={"fresh": "true"})) == (200, {"connected": True, "pro": True,
                                                                             "fresh": True, "effective_pro": True})
    assert call(routes.disconnect_route, Req()) == (200, {"connected": False, "note": None})


@pytest.mark.parametrize("error, code", [("HomeShed Pro has no pack with that name.", 404), ("Unknown pack.", 404),
                                         ("HomeShed Pro isn't connected here: paste your Pro key on the Pro page.", 400),
                                         ("The Pro website answered HTTP 500.", 502)])
def test_the_pack_route_maps_each_refusal(routes, monkeypatch, error, code):
    def refuse(slug):
        raise mavis_pro.MavisProError(error)
    monkeypatch.setattr(routes.mavis_pro, "pack", refuse)
    assert call(routes.pack_route, Req(slug="safe-operator")) == (code, {"error": error})


def test_the_packs_and_bugs_sync_routes(routes, monkeypatch):
    from tools.bugs import known, pro_sync
    monkeypatch.setattr(routes.mavis_pro, "packs", lambda: {"configured": True, "available": [], "error": None})
    monkeypatch.setattr(routes.mavis_pro, "pack", lambda slug: {"slug": slug})
    assert call(routes.packs_route, Req()) == (200, {"configured": True, "available": [], "error": None})
    assert call(routes.pack_route, Req(slug="safe-operator")) == (200, {"pack": {"slug": "safe-operator"}})
    monkeypatch.setattr(pro_sync, "sync", lambda: {"added": 2})
    assert call(routes.bugs_sync_route, Req()) == (200, {"added": 2})

    def refuse():
        raise known.BugsError("HomeShed Pro isn't connected here.")
    monkeypatch.setattr(pro_sync, "sync", refuse)
    assert call(routes.bugs_sync_route, Req()) == (400, {"error": "HomeShed Pro isn't connected here."})


@pytest.mark.parametrize("typed, used", [
    ("https://pro.example.com/api", "https://pro.example.com"),  # your own backend over https
    ("http://192.168.1.50:1337", "http://192.168.1.50:1337"),  # or on your own network
    ("http://pro.example.com", mavis_pro.DEFAULT_URL),  # plain http elsewhere: the key would show on the way
    ("https://someone:pw@pro.example.com", mavis_pro.DEFAULT_URL),
])
def test_the_pro_key_only_goes_to_a_safe_address(monkeypatch, typed, used):
    """R&D's security review, 2026-10-01 (confirmed): MAVIS_PRO_URL wasn't checked. An address the key mustn't go to
    is ignored for the official site, where the key belongs."""
    monkeypatch.setattr(mavis_pro.runtime_settings, "address", lambda name: typed)
    assert mavis_pro.base_url() == used
