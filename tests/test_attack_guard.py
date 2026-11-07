"""Our own attack tests on the door every request goes through (2026-11-06): the logic flaws a
scanner can't see. Each is something an attacker would try, sent through the real RequestGuard."""
import asyncio
import importlib
import sys

import pytest

import clients

OWNER = "owner-secret-owner-secret-owner-secret"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(clients, "CLIENTS_FILE", tmp_path / "clients.json")
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(clients, "_cache", (None, {"clients": {}}))
    for name in ("_windows", "_last_seen", "_touched", "_connections"):
        monkeypatch.setattr(clients, name, {})


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", OWNER)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost:8765")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "http://localhost:8765")
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    sys.modules.pop("server", None)
    server = importlib.import_module("server")

    async def app(scope, receive, send):
        scope["_passed"] = True

    return server.RequestGuard(app, OWNER, ["localhost:8765"])


def call(guard, path="/capabilities", auth=None, host=b"localhost:8765", method="GET", query=b"", extra=()):
    statuses = []

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    headers = [(b"host", host)] + ([(b"authorization", auth.encode("latin-1"))] if auth is not None else []) + list(extra)
    scope = {"type": "http", "method": method, "path": path, "query_string": query, "headers": headers,
             "client": ("203.0.113.5", 5000)}
    asyncio.run(guard(scope, None, send))
    return "passed" if scope.get("_passed") else statuses[0]


@pytest.mark.parametrize("auth", [
    None, "", "Bearer", "Bearer ", "Basic " + OWNER, "bearer " + OWNER, OWNER, "Bearer " + OWNER[:-1], "Bearer " + OWNER + "x",
    "Bearer  " + OWNER, "Bearer " + OWNER + " ", "Bearer hlc_", "Bearer hso_", "Bearer hso_guess", "Bearer null", "Bearer \x00",
    "Bearer \xff\xfe",
])
def test_no_near_miss_of_a_token_gets_in(guard, auth):
    assert call(guard, auth=auth) == 401


@pytest.mark.parametrize("where", [{"query": b"token=" + OWNER.encode()}, {"query": b"access_token=" + OWNER.encode()},
                                   {"extra": [(b"x-api-key", OWNER.encode())]},
                                   {"extra": [(b"cookie", b"token=" + OWNER.encode())]}])
def test_a_token_anywhere_but_the_header_does_not_count(guard, where):
    assert call(guard, **where) == 401


@pytest.mark.parametrize("host", [b"evil.example", b"", b"localhost:8765.evil.example", b"evil.example#localhost:8765",
                                  b"LOCALHOST:8765", b"localhost"])
def test_a_forged_host_is_refused_even_with_the_owner_token(guard, host):
    """DNS rebinding: a web page reaching the server through the visitor's own browser."""
    assert call(guard, auth="Bearer " + OWNER, host=host) == 421


@pytest.mark.parametrize("forged", [(b"x-forwarded-for", b"127.0.0.1"), (b"x-real-ip", b"127.0.0.1"),
                                    (b"cf-connecting-ip", b"127.0.0.1"), (b"x-forwarded-host", b"localhost:8765")])
def test_claiming_to_be_local_earns_nothing(guard, forged):
    assert call(guard, extra=[forged]) == 401


@pytest.mark.parametrize("path", ["/clients", "/capabilities", "/tools/memory.recall/disable", "/usage", "/MCP", "/",
                                  "/mcpadmin", "/mcp-admin", "/mcp/../clients", "/mcp/..%2fclients", "/mcp%2f../clients",
                                  "//mcp/../clients"])
def test_a_client_token_reaches_the_mcp_endpoint_only(guard, path):
    token = clients.create("app1", preset="read")
    assert call(guard, path=path, auth=f"Bearer {token}") == 403


def test_a_client_token_does_reach_the_mcp_endpoint(guard):
    token = clients.create("app1", preset="read")
    assert call(guard, path="/mcp", auth=f"Bearer {token}") == "passed"


@pytest.mark.parametrize("state", ["suspended", "expired"])
def test_a_shut_off_client_stays_out(guard, state, monkeypatch):
    token = clients.create("app1", preset="read")
    if state == "suspended":
        clients.set_suspended("app1", True)
    else:
        clients.create("app2", preset="read")
        monkeypatch.setattr(clients, "resolve", lambda a: ("app1", "expired"))
    assert call(guard, path="/mcp", auth=f"Bearer {token}") == 403


def test_a_websocket_is_closed_whatever_it_carries(guard):
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "websocket", "path": "/mcp", "headers": [(b"authorization", f"Bearer {OWNER}".encode())]}
    asyncio.run(guard(scope, receive, send))
    assert sent == [{"type": "websocket.close", "code": 1008}] and not scope.get("_passed")


def test_the_open_health_route_says_nothing_else_is_open(guard):
    assert call(guard, path="/healthz") == "passed"
    assert call(guard, path="/healthz", method="POST") == 401
    assert call(guard, path="/healthz/../clients") == 401
