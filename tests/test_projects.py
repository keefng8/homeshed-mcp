"""projects (which folder uses which memory agent) and its use by the memory tools and server.py. Case list drafted by
local_ai.ask (Qwen3-Coder-30B), reviewed; the owner-only cases added by hand (a client naming another project's folder
must never reach its memory)."""
import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

import pytest

import clients
import projects

SHARED = "agt-shared0001"
PREP = "agt-prep000001"
RND = "agt-rnd0000001"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(projects, "_cache", (None, []))
    monkeypatch.setattr(clients, "CLIENTS_FILE", tmp_path / "clients.json")
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(clients, "_cache", (None, {"clients": {}}))
    for name in ("_windows", "_last_seen", "_touched", "_connections"):
        monkeypatch.setattr(clients, name, {})
    for key, value in (("MEMORY_CORE_BASE_URL", "http://m"), ("MEMORY_CORE_BEARER", "b"), ("MEMORY_SERVICE_ID", "s"),
                       ("MEMORY_USER_KEY", "k"), ("MEMORY_TEAM_ID", "t"), ("MEMORY_USER_ID", "u"),
                       ("MEMORY_AGENT_ID", SHARED)):
        monkeypatch.setenv(key, value)
    return tmp_path


def register_both():
    projects.register("D:\\Work", "work", SHARED)
    projects.register("D:/Work/research", "research", RND)
    projects.register("D:/My App", "my-app", PREP)


# --- the registry --------------------------------------------------------------------------------------------------

def test_norm_makes_one_comparable_form():
    assert projects.norm("D:\\My App\\") == "d:/my app"
    assert projects.norm("file:///D:/My%20App") == "d:/my app"
    assert projects.norm("file:///home/alex/app") == "/home/alex/app"
    assert projects.norm("D:") == "d:/"
    for bad in ("", "relative/folder", None, "..\\x"):
        assert projects.norm(bad) == ""


def test_the_longest_registered_folder_wins_and_siblings_never_match():
    register_both()
    assert projects.agent_for("D:\\Work\\research\\evals") == RND
    assert projects.agent_for("d:/work/app") == SHARED
    assert projects.agent_for("D:/My App") == PREP
    assert projects.agent_for("D:/WorkOld") is None       # a sibling that shares a prefix
    assert projects.agent_for("E:/elsewhere") is None and projects.agent_for("") is None


def test_register_validates_replaces_and_persists(store):
    for path, name, agent in (("relative", "x", PREP), ("D:/x", "Bad Name", PREP), ("D:/x", "x", "prep"),
                              ("D:/x", "x", "agt-../y")):
        with pytest.raises(projects.ProjectError):
            projects.register(path, name, agent)
    projects.register("D:/My App", "my-app", PREP)
    projects.register("d:\\my app\\", "my-app", RND)  # the same folder: replaced, not added
    saved = json.loads((store / "projects.json").read_text(encoding="utf-8"))["projects"]
    assert [(r["path"], r["agent"]) for r in saved] == [("d:/my app", RND)]
    assert projects.remove("D:/MY APP") is True and projects.remove("D:/nowhere") is False
    assert projects.list_projects() == {"projects": [], "unreadable": False}


def test_an_unreadable_file_means_shared_memory_and_no_changes(store):
    (store / "projects.json").write_text("{not json", encoding="utf-8")
    assert projects.agent_for("D:/My App") is None
    assert projects.list_projects()["unreadable"] is True
    with pytest.raises(projects.ProjectError):
        projects.register("D:/My App", "my-app", PREP)  # would have wiped every other entry
    with pytest.raises(projects.ProjectError):
        projects.remove("D:/My App")


# --- which memory a call uses -----------------------------------------------------------------------------------

def agent(**kw):
    from tools.memory.capture import _config
    return _config(**kw)["agent_id"]


def test_the_owner_uses_the_project_it_names():
    register_both()
    assert agent() == SHARED                                          # no folder: shared, as before
    assert agent(project_dir="D:\\My App") == PREP
    assert agent(project_dir="C:/somewhere/unregistered") == SHARED
    assert agent(scope="global", project_dir="D:/My App") == SHARED  # global is always the shared one
    token = projects.current_dir.set("D:/My App")           # the X-Homelab-Project header
    try:
        assert agent() == PREP
        assert agent(project_dir="D:/Work/research") == RND  # the argument beats the header
    finally:
        projects.current_dir.reset(token)


def test_a_client_can_never_reach_another_projects_memory_by_naming_its_folder():
    register_both()
    clients.create("app1", "", "all")
    clients.set_memory_agent("app1", "agt-example123")
    clients.create("friend", "", "read")
    clients.set_memory_agent("friend", "agt-friend0001")  # an app without one gets its own: test_memory_apps.py
    for name, expected in (("app1", "agt-example123"), ("friend", "agt-friend0001")):
        token = clients.current_client.set(name)
        try:
            assert agent(project_dir="D:/My App") == expected
            header = projects.current_dir.set("D:/My App")
            try:
                assert agent() == expected
            finally:
                projects.current_dir.reset(header)
        finally:
            clients.current_client.reset(token)


def test_the_memory_tools_pass_the_folder_through(monkeypatch):
    register_both()
    import httpx
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json["agent_id"])
        body = {"code": 0, "data": {"messages": [], "accepted_ids": ["m1"]}}
        return SimpleNamespace(status_code=200, json=lambda: body)

    monkeypatch.setattr(httpx, "post", fake_post)
    from tools.memory.recall_facts import recall_facts
    from tools.memory.recall_relevant import recall_relevant
    from tools.memory.remember_fact import remember_fact
    remember_fact("the prepper keeps its own memory", "platform", project_dir="D:/My App")
    recall_facts("platform", project_dir="D:/My App")
    recall_relevant("s1", "memory", project_dir="D:/My App")
    assert sent and set(sent) == {PREP}
    assert projects.current_dir.get() == ""                           # nothing leaks past the call


# --- server.py -------------------------------------------------------------------------------------------------------

@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    return importlib.import_module("server")


def _ctx(auth, project=""):
    headers = {"authorization": auth, **({projects.HEADER: project} if project else {})}
    return SimpleNamespace(request=SimpleNamespace(headers=headers, client=SimpleNamespace(host="127.0.0.1")))


def test_only_the_owners_calls_carry_the_project_header(server, monkeypatch):
    seen = []

    async def fake_call(self, ctx, params):
        seen.append(projects.current_dir.get())
        return "done"

    monkeypatch.setattr(server.MCPServer, "_handle_call_tool", fake_call)
    call = SimpleNamespace(name="memory.recall")
    asyncio.run(server.mcp._handle_call_tool(_ctx("Bearer owner-secret", "D:/My App"), call))
    token = clients.create("app1", "", "all")
    asyncio.run(server.mcp._handle_call_tool(_ctx(f"Bearer {token}", "D:/My App"), call))
    assert seen == ["D:/My App", ""]
    assert projects.current_dir.get() == ""


def test_the_memory_tools_say_what_project_dir_is(server):
    tools = {t.name: t.description for t in asyncio.run(server.mcp.list_tools())}
    for name in ("memory.recall", "memory.recall_facts", "memory.recall_relevant", "memory.remember_fact",
                 "memory.capture"):
        assert "project_dir:" in tools[name], name
    assert "project_dir" not in tools["system.health"]
