"""Memory per app (the owner's decision, 2026-10-01): each app has its own memory; reading the shared memory is on by
default with a switch per app; an app's writes to the shared memory wait for the owner's approval (memory_pending.py).
Case list drafted by local_ai.ask (Qwen3-Coder-30B), checked and extended by Claude (a failed approval stays waiting,
an unreadable queue refuses rather than empties, the owner's own routes)."""
import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

import pytest

import clients
import memory_pending
import vault
from tools.memory import capture as cap
from tools.memory.capture import MemoryError, capture
from tools.memory.recall import recall
from tools.memory.recall_facts import recall_facts
from tools.memory.recall_relevant import fact_categories
from tools.memory.remember_fact import remember_fact


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    """The built-in store (no memory-core), and fresh client and waiting-write files, all in a temp folder."""
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setattr(vault, "secret", lambda name, default=None: None)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(clients, "CLIENTS_FILE", tmp_path / "clients.json")
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(clients, "_cache", (None, {"clients": {}}))
    for name in ("_windows", "_last_seen", "_touched", "_connections"):
        monkeypatch.setattr(clients, name, {})
    monkeypatch.setattr(memory_pending, "PENDING_FILE", tmp_path / "memory_pending.json")
    for name in ("app1", "app2"):
        clients.create(name, "", "all")
    return tmp_path


class as_app:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.token = clients.current_client.set(self.name)

    def __exit__(self, *exc):
        clients.current_client.reset(self.token)


def memory_core(monkeypatch, answer=None, fail=False):
    """memory-core configured; agent creation answers `answer` (or fails). Returns the list of create calls."""
    values = {"MEMORY_CORE_BASE_URL": "http://m", "MEMORY_CORE_BEARER": "b", "MEMORY_SERVICE_ID": "s",
              "MEMORY_USER_KEY": "k", "MEMORY_TEAM_ID": "t", "MEMORY_USER_ID": "u", "MEMORY_AGENT_ID": "agt-shared0001"}
    monkeypatch.setattr(vault, "secret", lambda name, default=None: values.get(name))
    calls = []

    def post(url, **kw):
        calls.append((url, kw["json"]))
        if fail:
            raise cap.httpx.ConnectError("down")
        return SimpleNamespace(status_code=200, json=lambda: answer)
    monkeypatch.setattr(cap.httpx, "post", post)
    return calls


# --- its own memory -------------------------------------------------------------------------------------------------

def test_on_the_built_in_store_each_app_has_its_own_memory():
    with as_app("app1"):
        capture("notes", "app1's note")
        assert cap._config()["agent_id"] == "app:app1"
    with as_app("app2"):
        assert recall("notes")["messages"] == []  # app2 can't see app1's memory
    assert recall("notes")["messages"] == []      # nor the owner's shared memory
    with as_app("app1"):
        assert [m["content"] for m in recall("notes")["messages"]] == ["app1's note"]


def test_an_agent_the_owner_set_still_wins():
    clients.set_memory_agent("app1", "agt-ownagent1")
    with as_app("app1"):
        assert cap._config()["agent_id"] == "agt-ownagent1"


def test_memory_core_makes_an_apps_agent_once_and_keeps_it(monkeypatch):
    calls = memory_core(monkeypatch, {"code": 0, "data": {"agent_id": "agt-made00001"}})
    with as_app("app1"):
        assert cap._config()["agent_id"] == "agt-made00001"
        assert cap._config()["agent_id"] == "agt-made00001"
    assert len(calls) == 1 and calls[0][0] == "http://m/v3/meta/agent/create"
    assert calls[0][1]["team_id"] == "t" and calls[0][1]["owner_user_id"] == "u" and calls[0][1]["name"].startswith("app-app1-")
    assert clients.memory_agent_for("app1") == "agt-made00001"
    assert cap._config()["agent_id"] == "agt-shared0001"  # the owner is unchanged


@pytest.mark.parametrize("answer, fail", [({"code": 1, "message": "quota"}, False), ({"code": 0, "data": {}}, False),
                                          ({"code": 0, "data": {"agent_id": "../shared"}}, False), (None, True)])
def test_if_memory_core_wont_make_one_the_call_fails_and_never_uses_the_shared_agent(monkeypatch, answer, fail):
    memory_core(monkeypatch, answer, fail)
    with as_app("app1"), pytest.raises(MemoryError, match="never uses the shared memory"):
        cap._config()
    assert clients.memory_agent_for("app1") is None


# --- reading the shared memory ----------------------------------------------------------------------------------------

def test_apps_read_the_shared_memory_unless_the_owner_switches_it_off():
    capture("platform", "a shared fact", scope="global")  # the owner's own write goes straight in
    with as_app("app1"):
        assert [m["content"] for m in recall("platform", scope="global")["messages"]] == ["a shared fact"]
    clients.set_memory_shared_read("app1", False)
    with as_app("app1"), pytest.raises(MemoryError, match="switched that off"):
        recall("platform", scope="global")
    with as_app("app1"):
        assert recall("platform")["messages"] == []  # its own memory still works
    with as_app("app2"):
        assert recall("platform", scope="global")["total"] == 1  # the switch is per app
    assert recall("platform", scope="global")["total"] == 1      # and never the owner's
    rows = {c["name"]: c["memory_shared_read"] for c in clients.list_clients()["clients"]}
    assert rows == {"app1": False, "app2": True}
    assert any(a["action"] == "shared memory reads off" and a["client"] == "app1" for a in clients.audit_trail())
    clients.set_memory_shared_read("app1", True)
    with as_app("app1"):
        assert recall("platform", scope="global")["total"] == 1


# --- writing the shared memory: it waits for the owner ---------------------------------------------------------------

def test_an_apps_shared_write_waits_and_writes_nothing():
    with as_app("app1"):
        out = capture("notes", "please share this", scope="global")
    assert out["accepted"] is False and out["message_id"] is None and "owner" in out["note"]
    assert recall("notes", scope="global")["messages"] == []
    (item,) = memory_pending.list_pending()
    assert item["id"] == out["pending"] and item["client"] == "app1" and item["content"] == "please share this"


def test_approving_writes_it_as_the_owners_and_indexes_a_new_fact_category():
    with as_app("app1"):
        out = remember_fact("deploys go through the script", category="decisions", scope="global")
        assert out["pending"] and recall_facts("decisions", scope="global")["messages"] == []
    assert "decisions" not in fact_categories("global")
    done = memory_pending.decide(out["pending"], True)
    assert done["approved"] and done["stored"]["accepted"]
    assert [m["content"] for m in recall_facts("decisions", scope="global")["messages"]] == ["deploys go through the script"]
    assert "decisions" in fact_categories("global") and memory_pending.list_pending() == []
    assert any(a["action"] == "shared memory write approved" and a["client"] == "app1" for a in clients.audit_trail())


def test_declining_drops_it_and_a_decided_write_cant_be_decided_again():
    with as_app("app1"):
        item = capture("notes", "no thanks", scope="global")["pending"]
    assert memory_pending.decide(item, False) == {"id": item, "approved": False}
    assert recall("notes", scope="global")["messages"] == [] and memory_pending.list_pending() == []
    with pytest.raises(memory_pending.PendingError, match="no write"):
        memory_pending.decide(item, True)
    for bad in ("zz", "../../x", "DEADBEEF", ""):
        with pytest.raises(memory_pending.PendingError):
            memory_pending.decide(bad, True)


def test_the_owners_own_shared_write_goes_straight_in():
    assert capture("notes", "from the owner", scope="global")["accepted"] is True
    assert recall("notes", scope="global")["total"] == 1 and memory_pending.list_pending() == []


def test_the_queue_is_capped_per_app_and_in_all(monkeypatch):
    monkeypatch.setattr(memory_pending, "MAX_PER_APP", 2)
    monkeypatch.setattr(memory_pending, "MAX_TOTAL", 3)
    with as_app("app1"):
        capture("n", "1", scope="global")
        capture("n", "2", scope="global")
        with pytest.raises(MemoryError, match="this app already has 2"):
            capture("n", "3", scope="global")
    with as_app("app2"):
        capture("n", "4", scope="global")
        with pytest.raises(MemoryError, match="3 writes"):
            capture("n", "5", scope="global")
    assert len(memory_pending.list_pending()) == 3


def test_an_approval_whose_write_fails_stays_waiting(monkeypatch):
    with as_app("app1"):
        item = capture("notes", "keep me", scope="global")["pending"]

    def broken(*a, **kw):
        raise MemoryError("memory-core is unavailable")
    monkeypatch.setattr(cap, "capture", broken)
    with pytest.raises(MemoryError):
        memory_pending.decide(item, True)
    assert [i["id"] for i in memory_pending.list_pending()] == [item]


def test_an_unreadable_queue_refuses_rather_than_starting_empty(stores):
    (stores / "memory_pending.json").write_text("{not json", encoding="utf-8")
    with as_app("app1"), pytest.raises(MemoryError, match="unreadable"):
        capture("notes", "x", scope="global")
    with pytest.raises(memory_pending.PendingError, match="unreadable"):
        memory_pending.list_pending()


def test_a_bad_scope_is_refused():
    with pytest.raises(MemoryError, match="scope"):
        capture("notes", "x", scope="everyone")


# --- the owner's routes -----------------------------------------------------------------------------------------------

@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    return importlib.import_module("server")


def _call(route, **params):
    resp = asyncio.run(route(SimpleNamespace(path_params=params, query_params={})))
    return resp.status_code, json.loads(resp.body)


def test_the_owner_lists_approves_and_declines_through_the_routes(server):
    with as_app("app1"):
        keep = capture("notes", "share me", scope="global")["pending"]
        drop = capture("notes", "not me", scope="global")["pending"]
    status, body = _call(server.memory_pending_list)
    assert status == 200 and {i["id"] for i in body["items"]} == {keep, drop} and body["most_per_app"] == 50
    assert _call(server.memory_pending_decide, item_id=keep, action="approve")[0] == 200
    assert _call(server.memory_pending_decide, item_id=drop, action="decline")[0] == 200
    assert [m["content"] for m in recall("notes", scope="global")["messages"]] == ["share me"]
    assert _call(server.memory_pending_decide, item_id=keep, action="approve")[0] == 404
    assert _call(server.memory_pending_decide, item_id=keep, action="publish")[0] == 400


def test_the_owner_switches_an_apps_shared_reads_through_the_client_route(server):
    async def body():
        return {}
    req = SimpleNamespace(path_params={"name": "app1", "action": "shared-read-off"}, json=body, body=lambda: b"")
    resp = asyncio.run(server.clients_action(req))
    assert resp.status_code == 200 and clients.shared_read_for("app1") is False
    req.path_params["action"] = "shared-read-on"
    assert asyncio.run(server.clients_action(req)).status_code == 200 and clients.shared_read_for("app1") is True
