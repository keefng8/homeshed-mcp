"""clients (per-client tool access) + the RequestGuard and GuardedMCPServer that enforce it. Case
list drafted by local_ai.ask (Qwen3-Coder-30B), checked against the real behaviour (a suspended or
expired client resolves to its name with that status, not None)."""
import asyncio
import importlib
import json
import sys
import time
from types import SimpleNamespace

import pytest

import clients


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(clients, "CLIENTS_FILE", tmp_path / "clients.json")
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(clients, "_cache", (None, {"clients": {}}))
    for name in ("_windows", "_last_seen", "_touched", "_connections"):
        monkeypatch.setattr(clients, name, {})
    return tmp_path


def bearer(token):
    return f"Bearer {token}"


# --- create / resolve / storage -----------------------------------------------------------------

def test_create_returns_prefixed_token_stored_only_as_hash(store):
    token = clients.create("app1", "Example app", "read", None, 120)
    raw = (store / "clients.json").read_text()
    assert token.startswith("hlc_") and token not in raw and clients._hash(token) in raw
    assert clients.resolve(bearer(token)) == ("app1", "ok")


def test_create_records_note_expiry_and_rate(store):
    clients.create("friend", "weekend access", "none", 7, 30)
    c = clients.list_clients()["clients"][0]
    assert c["note"] == "weekend access" and c["rate_per_min"] == 30 and c["expires"] > time.time() + 6 * 86400


@pytest.mark.parametrize("name", ["Friend", "1abc", "a", "has space", "x" * 40, ""])
def test_create_rejects_bad_names(name):
    with pytest.raises(clients.ClientError):
        clients.create(name)


def test_owner_is_a_reserved_name():
    with pytest.raises(clients.ClientError, match="reserved"):
        clients.create("owner")


def test_create_rejects_duplicates_and_bad_values():
    clients.create("app1")
    with pytest.raises(clients.ClientError, match="already exists"):
        clients.create("app1")
    with pytest.raises(clients.ClientError):
        clients.create("other", preset="root")
    with pytest.raises(clients.ClientError):
        clients.create("other", rate_per_min=0)


def test_resolve_unknown_and_malformed_headers():
    clients.create("app1")
    assert clients.resolve(bearer("hlc_not-a-real-token")) == (None, "unknown")
    assert clients.resolve("Basic abc") == (None, "unknown")
    assert clients.resolve(None) == (None, "unknown")


def test_suspended_and_expired_resolve_with_their_status(store):
    token = clients.create("app1")
    clients.set_suspended("app1", True)
    assert clients.resolve(bearer(token)) == ("app1", "suspended")
    clients.set_suspended("app1", False)
    data = json.loads((store / "clients.json").read_text())
    data["clients"]["app1"]["expires"] = time.time() - 1
    (store / "clients.json").write_text(json.dumps(data))
    assert clients.resolve(bearer(token)) == ("app1", "expired")


def test_corrupt_file_fails_closed_and_refuses_writes(store):
    token = clients.create("app1")
    (store / "clients.json").write_text("{corrupt")
    assert clients.resolve(bearer(token)) == (None, "unreadable")
    assert clients.list_clients()["unreadable"] is True
    with pytest.raises(clients.ClientError, match="unreadable"):
        clients.create("other")


# --- grants ---------------------------------------------------------------------------------

@pytest.mark.parametrize("pattern, tool, risk, category, expected", [
    ("*", "docker.container.stop", "write", "docker", True),
    ("read:*", "memory.recall", "read", "memory", True),
    ("read:*", "docker.container.stop", "write", "docker", False),
    ("read:memory", "memory.recall", "read", "memory", True),
    ("read:memory", "knowledge.search", "read", "knowledge", False),
    ("memory.*", "memory.remember_fact", "write", "memory", True),
    ("memory.*", "local_ai.ask", "read", "local_ai", False),
    ("local_ai.ask", "local_ai.ask", "read", "local_ai", True),
])
def test_grant_patterns(pattern, tool, risk, category, expected):
    assert clients._allowed({"allow": [pattern], "deny": []}, tool, risk, category) is expected


def test_presets():
    clients.create("none-client", preset="none")
    clients.create("read-client", preset="read")
    clients.create("all-client", preset="all")
    assert not clients.is_allowed("none-client", "memory.recall", "read", "memory")
    assert clients.is_allowed("read-client", "memory.recall", "read", "memory")
    assert not clients.is_allowed("read-client", "docker.container.stop", "write", "docker")
    assert clients.is_allowed("all-client", "docker.container.stop", "write", "docker")


def test_grant_and_revoke_one_tool_including_pattern_allowed():
    clients.create("app1", preset="read")
    clients.grant("app1", "docker.container.stop", "write", "docker")
    assert clients.is_allowed("app1", "docker.container.stop", "write", "docker")
    clients.revoke("app1", "memory.recall", "read", "memory")  # allowed via read:* -> needs a deny entry
    assert not clients.is_allowed("app1", "memory.recall", "read", "memory")
    assert clients.is_allowed("app1", "memory.recall_facts", "read", "memory")
    clients.grant("app1", "memory.recall", "read", "memory")  # re-grant removes the deny
    assert clients.is_allowed("app1", "memory.recall", "read", "memory")


def test_check_call_enforces_grants_and_rate_limit():
    clients.create("app1", preset="read", rate_per_min=2)
    assert clients.check_call("app1", "docker.container.stop", "write", "docker")[0] is False
    results = [clients.check_call("app1", "memory.recall", "read", "memory")[0] for _ in range(3)]
    assert results == [True, True, False]
    assert "limit of 2 calls per minute" in clients.check_call("app1", "memory.recall", "read", "memory")[1]


# --- lifecycle + audit --------------------------------------------------------------------------

def test_rotate_invalidates_old_token():
    old = clients.create("app1")
    new = clients.rotate("app1")
    assert clients.resolve(bearer(old)) == (None, "unknown") and clients.resolve(bearer(new)) == ("app1", "ok")


def test_a_write_caches_what_it_wrote():
    """A same-size rewrite inside one clock tick leaves (mtime, size) unchanged, and the cache then kept an old token
    working (seen as a flaky test_rotate_invalidates_old_token, 2026-09-29). So a write caches what it wrote."""
    old = clients.create("app1")
    assert clients.resolve(bearer(old)) == ("app1", "ok")  # primes the cache
    new = clients.rotate("app1")
    assert clients._cache[1]["clients"]["app1"]["token_sha256"] == clients._hash(new)
    assert clients._cache[0][0] == str(clients.CLIENTS_FILE)  # keyed on the file too: tests move it per test


def test_suspend_all_delete_and_limits():
    t1, t2 = clients.create("a1"), clients.create("a2")
    assert clients.suspend_all() == 2
    assert clients.resolve(bearer(t1))[1] == clients.resolve(bearer(t2))[1] == "suspended"
    clients.set_suspended("a1", False)
    clients.set_limits("a1", None, 30)
    c = next(x for x in clients.list_clients()["clients"] if x["name"] == "a1")
    assert c["rate_per_min"] is None and c["expires"] > time.time() + 29 * 86400
    clients.delete("a1")
    assert clients.resolve(bearer(t1)) == (None, "unknown")
    with pytest.raises(clients.ClientError, match="no client"):
        clients.delete("a1")


def test_audit_trail_records_actions_never_tokens(store):
    token = clients.create("app1", preset="read")
    new = clients.rotate("app1")
    clients.grant("app1", "docker.container.stop", "write", "docker")
    trail = clients.audit_trail()
    assert [e["action"] for e in trail][:3] == ["granted", "token rotated", "created"]
    text = (store / "audit.jsonl").read_text()
    assert token not in text and new not in text


# --- enforcement in server.py ---------------------------------------------------------------------

@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    return importlib.import_module("server")


def _guard(server, path, auth):
    seen = {}

    async def app(scope, receive, send):
        seen["passed"] = True

    async def send(message):
        if message["type"] == "http.response.start":
            seen["status"] = message["status"]

    guard = server.RequestGuard(app, "owner-secret", ["localhost"])
    scope = {"type": "http", "path": path, "headers": [(b"host", b"localhost"), (b"authorization", auth.encode())]}
    asyncio.run(guard(scope, None, send))
    return "passed" if seen.get("passed") else seen.get("status")


def test_request_guard_paths_and_statuses(server):
    token = clients.create("app1", preset="read")
    assert _guard(server, "/clients", "Bearer owner-secret") == "passed"
    assert _guard(server, "/mcp", bearer(token)) == "passed"
    assert _guard(server, "/clients", bearer(token)) == 403
    assert _guard(server, "/tools/memory.recall/disable", bearer(token)) == 403
    assert _guard(server, "/mcp", bearer("hlc_wrong")) == 401
    clients.set_suspended("app1", True)
    assert _guard(server, "/mcp", bearer(token)) == 403


def test_wrong_tokens_from_one_address_are_throttled_but_never_the_owner(server):
    """R&D's security review, 2026-10-01: the owner token was compared with == (timing), and wrong tokens could be
    tried without limit. After FAIL_MAX wrong ones in FAIL_WINDOW_S an address gets 429; the owner still gets in."""
    seen = {}

    async def app(scope, receive, send):
        seen["passed"] = True

    async def send(message):
        if message["type"] == "http.response.start":
            seen["status"] = message["status"]

    guard = server.RequestGuard(app, "owner-secret", ["localhost"])

    def call(auth, ip="10.0.0.7"):
        seen.clear()
        scope = {"type": "http", "path": "/mcp", "client": (ip, 5000),
                 "headers": [(b"host", b"localhost"), (b"authorization", auth.encode())]}
        asyncio.run(guard(scope, None, send))
        return "passed" if seen.get("passed") else seen.get("status")

    statuses = [call(bearer(f"hlc_guess{k}")) for k in range(guard.FAIL_MAX + 2)]
    assert statuses[:guard.FAIL_MAX] == [401] * guard.FAIL_MAX and statuses[-1] == 429
    assert call("Bearer owner-secret") == "passed"  # the owner is checked first: never locked out
    assert call(bearer("hlc_guess"), ip="10.0.0.8") == 401  # another address is unaffected


def test_the_studio_service_token_reaches_its_one_route_only(server, monkeypatch):
    import vault
    token = "s" * 64
    monkeypatch.setattr(vault, "secret", lambda name, default=None: token if name == "STUDIO_SERVICE_TOKEN" else default)
    assert _guard(server, "/voice/natural", bearer(token)) == "passed"
    for path in ("/voice/speak", "/vault", "/settings", "/mcp"):
        assert _guard(server, path, bearer(token)) == 401
    assert _guard(server, "/voice/natural", bearer("s" * 63 + "t")) == 401
    monkeypatch.setattr(vault, "secret", lambda name, default=None: "short")
    assert _guard(server, "/voice/natural", bearer("short")) == 401  # too short to trust
    monkeypatch.setattr(vault, "secret", lambda name, default=None: None)
    assert _guard(server, "/voice/natural", "Bearer ") == 401  # no token made yet: nothing matches


def _ctx(auth, host=None):
    return SimpleNamespace(request=SimpleNamespace(headers={"authorization": auth},
                                                   client=SimpleNamespace(host=host) if host else None))


def test_client_sees_only_granted_tools(server):
    token = clients.create("app1", preset="read")
    clients.grant("app1", "docker.container.stop", "write", "docker")
    owner = asyncio.run(server.mcp._handle_list_tools(_ctx("Bearer owner-secret"), None)).tools
    mine = asyncio.run(server.mcp._handle_list_tools(_ctx(bearer(token)), None)).tools
    names = {t.name for t in mine}
    assert len(owner) > len(mine) > 0
    assert "docker.container.stop" in names and "docker.container.restart" not in names


def test_ungranted_call_is_refused_and_logged_with_client(server):
    token = clients.create("app1", preset="read")
    result = asyncio.run(server.mcp._handle_call_tool(_ctx(bearer(token)), SimpleNamespace(name="docker.container.restart", arguments={})))
    assert result.is_error and "not granted" in result.content[0].text
    from registry import get_activity_log
    last = get_activity_log()[0]
    assert last["id"] == "docker.container.restart" and last["client"] == "app1" and last["refused"] is True


# --- live connections (Clients live panel) ----------------------------------------------------------
# A connection is one caller on one machine: the newest MCP protocol (Claude Code's) sends no session
# id. Case list drafted by local_ai.ask (Qwen3-Coder-30B), checked against the real behaviour.

def test_connection_tag_is_short_and_stable():
    tag = clients.connection_tag("app1", "192.168.1.20")
    assert len(tag) == 6 and tag == clients.connection_tag("app1", "192.168.1.20")
    assert clients.connection_tag(None, "192.168.1.20") == clients.connection_tag("owner", "192.168.1.20")
    assert clients.connection_tag("app1", None) is None


def test_two_callers_on_one_machine_and_one_caller_on_two_machines():
    a = clients.seen(None, "192.168.1.20", call=True)
    b = clients.seen("app1", "192.168.1.20", call=True)
    c = clients.seen("app1", "192.168.1.50", call=True)
    assert len({a, b, c}) == 3
    rows = {(r["caller"], r["host"]) for r in clients.active_connections()}
    assert rows == {("owner", "192.168.1.20"), ("app1", "192.168.1.20"), ("app1", "192.168.1.50")}


def test_listing_marks_a_connection_and_calls_count():
    tag = clients.seen(None, "10.0.0.5", call=False)
    (row,) = clients.active_connections()
    assert row["tag"] == tag and row["caller"] == "owner" and row["calls"] == 0
    clients.seen(None, "10.0.0.5", call=True)
    clients.seen(None, "10.0.0.5", call=True)
    assert clients.active_connections()[0]["calls"] == 2
    assert clients.seen("app1", None, call=True) is None  # unknown source: nothing recorded
    assert len(clients.active_connections()) == 1


def test_active_connections_window_and_newest_first():
    old = clients.seen("app1", "10.0.0.1", call=True)
    mid = clients.seen(None, "10.0.0.2", call=True)
    new = clients.seen("app1", "10.0.0.3", call=True)
    clients._connections[old]["last"] -= clients.CONNECTION_WINDOW_S + 1
    clients._connections[mid]["last"] -= 5
    assert [r["tag"] for r in clients.active_connections()] == [new, mid]


def test_idle_connections_are_forgotten_once_the_table_is_large(monkeypatch):
    monkeypatch.setattr(clients, "CONNECTION_PRUNE_AT", 3)
    for i in range(3):
        clients._connections[clients.seen(None, f"10.0.1.{i}", call=True)]["last"] -= clients.CONNECTION_FORGET_S + 1
    fresh = clients.seen(None, "10.0.2.1", call=True)
    assert list(clients._connections) == [fresh]


def test_server_notes_connections_per_caller(server):
    token = clients.create("app1", preset="read")
    asyncio.run(server.mcp._handle_list_tools(_ctx("Bearer owner-secret", "192.168.1.20"), None))
    asyncio.run(server.mcp._handle_list_tools(_ctx(bearer(token), "192.168.1.50"), None))
    asyncio.run(server.mcp._handle_list_tools(_ctx(bearer("hlc_wrong"), "192.168.1.66"), None))
    rows = {(r["caller"], r["host"]) for r in clients.active_connections()}
    assert rows == {("owner", "192.168.1.20"), ("app1", "192.168.1.50")}


def test_refused_call_is_logged_with_its_connection(server):
    token = clients.create("app1", preset="read")
    asyncio.run(server.mcp._handle_call_tool(_ctx(bearer(token), "192.168.1.50"),
                                             SimpleNamespace(name="docker.container.restart", arguments={})))
    from registry import get_activity_log
    last = get_activity_log()[0]
    assert last["refused"] is True and last["conn"] == clients.connection_tag("app1", "192.168.1.50")
    assert clients.active_connections() == []  # a refused call doesn't count as a connection


def test_connections_route_and_owner_only(server):
    token = clients.create("app1", preset="all")
    clients.create("gone")
    clients.delete("gone")
    clients.seen("app1", "192.168.1.50", call=True)
    body = json.loads(asyncio.run(server.list_connections(None)).body)
    assert body["window_s"] == clients.CONNECTION_WINDOW_S and body["clients"] == ["app1"]
    assert body["connections"][0]["caller"] == "app1" and body["connections"][0]["host"] == "192.168.1.50"
    assert _guard(server, "/connections", bearer(token)) == 403
    assert _guard(server, "/connections", "Bearer owner-secret") == "passed"


# --- per-client memory agent (2026-09-28: every project's memory had been landing in the shared agent) ---

def test_memory_agent_is_set_validated_listed_and_cleared(store):
    clients.create("app1", "", "all")
    assert clients.memory_agent_for("app1") is None and clients.memory_agent_for(None) is None
    clients.set_memory_agent("app1", "agt-example123")
    assert clients.memory_agent_for("app1") == "agt-example123"
    assert clients.list_clients()["clients"][0]["memory_agent"] == "agt-example123"
    for bad in ("dis5r3zslk", "agt-../x", "agt-A B"):
        with pytest.raises(clients.ClientError):
            clients.set_memory_agent("app1", bad)
    clients.set_memory_agent("app1", None)
    assert clients.memory_agent_for("app1") is None
    with pytest.raises(clients.ClientError):
        clients.set_memory_agent("nobody", "agt-abcd1234")


def test_a_clients_memory_calls_use_its_own_agent(store, monkeypatch):
    for key, value in (("MEMORY_CORE_BASE_URL", "http://m"), ("MEMORY_CORE_BEARER", "b"), ("MEMORY_SERVICE_ID", "s"),
                       ("MEMORY_USER_KEY", "k"), ("MEMORY_TEAM_ID", "t"), ("MEMORY_USER_ID", "u"), ("MEMORY_AGENT_ID", "agt-shared")):
        monkeypatch.setenv(key, value)
    from tools.memory.capture import _config
    clients.create("app1", "", "all")
    clients.set_memory_agent("app1", "agt-example123")
    assert _config()["agent_id"] == "agt-shared"                  # the owner: shared agent
    token = clients.current_client.set("app1")
    try:
        assert _config()["agent_id"] == "agt-example123"          # the client: its project's own
    finally:
        clients.current_client.reset(token)
    clients.create("friend", "", "read")
    import tools.memory.capture as cap
    monkeypatch.setattr(cap.httpx, "post", lambda url, **kw: SimpleNamespace(
        status_code=200, json=lambda: {"code": 0, "data": {"agent_id": "agt-friend0001"}}))
    token = clients.current_client.set("friend")
    try:
        assert _config()["agent_id"] == "agt-friend0001"          # a client without one: its own, made now (2026-10-01)
    finally:
        clients.current_client.reset(token)
    assert clients.memory_agent_for("friend") == "agt-friend0001"


def test_global_scope_reads_the_shared_agent_whoever_asks(store, monkeypatch):
    for key, value in (("MEMORY_CORE_BASE_URL", "http://m"), ("MEMORY_CORE_BEARER", "b"), ("MEMORY_SERVICE_ID", "s"),
                       ("MEMORY_USER_KEY", "k"), ("MEMORY_TEAM_ID", "t"), ("MEMORY_USER_ID", "u"), ("MEMORY_AGENT_ID", "agt-shared")):
        monkeypatch.setenv(key, value)
    from tools.memory.capture import MemoryError, _config
    clients.create("app1", "", "all")
    clients.set_memory_agent("app1", "agt-example123")
    token = clients.current_client.set("app1")
    try:
        assert _config("project")["agent_id"] == "agt-example123"
        assert _config("global")["agent_id"] == "agt-shared"      # the shared platform facts
        with pytest.raises(MemoryError):
            _config("everything")
    finally:
        clients.current_client.reset(token)


def test_the_read_tools_pass_scope_through(monkeypatch):
    from tools.memory import recall_facts as rf
    seen = {}
    monkeypatch.setattr(rf, "recall", lambda session_id, limit, max_chars, scope, project_dir="":
                        seen.update(scope=scope) or {"messages": []})
    rf.recall_facts("platform", scope="global")
    assert seen == {"scope": "global"}


def test_request_guard_refuses_websockets_and_lets_lifespan_through(server):
    """Non-HTTP scopes used to skip every check (R21). A websocket, even with the owner's token, is closed before the
    app sees it; the server's own start-up and shut-down (lifespan) still pass."""
    passed, sent = [], []

    async def app(scope, receive, send):
        passed.append(scope["type"])

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    guard = server.RequestGuard(app, "owner-secret", ["localhost"])
    ws = {"type": "websocket", "path": "/mcp", "headers": [(b"host", b"localhost"), (b"authorization", b"Bearer owner-secret")]}
    asyncio.run(guard(ws, receive, send))
    assert passed == [] and sent == [{"type": "websocket.close", "code": 1008}]
    asyncio.run(guard({"type": "lifespan"}, receive, send))
    assert passed == ["lifespan"]


def _as_client(name, tool):
    """What server.py sets for a client's MCP call to `tool`: the caller and the tool it named."""
    return clients.current_client.set(name), clients.current_tool.set(tool)


def test_a_workflow_step_needs_its_own_grant(server):
    """R&D F2 (2026-09-30): a client granted only workflow.run could run any enabled tool as a step."""
    from registry import get_activity_log, get_capability
    clients.create("app1", "", "none")
    clients.grant("app1", "workflow.run", *server._tool_meta("workflow.run"))
    run = get_capability("workflow.run").func  # the served wrapper, as an MCP call reaches it
    who, tool = _as_client("app1", "workflow.run")
    try:
        out = run(steps=[{"capability": "system.info"}])
        assert out["ok"] is False and "not granted" in out["steps"][0]["error"]
        assert any(e["id"] == "system.info" and e.get("refused") and e["client"] == "app1"
                   for e in get_activity_log()[:3])  # logged as a refusal, like an ungranted named tool
        clients.grant("app1", "system.info", *server._tool_meta("system.info"))
        assert run(steps=[{"capability": "system.info"}])["ok"] is True  # granted: the step runs
    finally:
        clients.current_tool.reset(tool)
        clients.current_client.reset(who)


def test_the_owner_and_the_named_tool_are_not_checked_twice(server, monkeypatch):
    """The named tool was checked (and counted) by server.py; the owner has every tool. Neither pays again."""
    from registry import get_capability
    calls = []
    monkeypatch.setattr(clients, "check_call", lambda *a: calls.append(a) or (True, ""))
    info = get_capability("system.info").func
    info()                                    # the owner: no client set
    who, tool = _as_client("app1", "system.info")
    try:
        info()                                # the very tool the client named
    finally:
        clients.current_tool.reset(tool)
        clients.current_client.reset(who)
    assert calls == []
