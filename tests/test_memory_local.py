"""The built-in memory store (memory_local.py) and the memory tools on it when no memory-core is configured
(release v1, 2026-09-29). Case list drafted by local_ai.ask (Qwen3-Coder-30B), corrected by Claude: count() is the
whole store's total, and limits are checked by memory.recall before the store is asked."""
import re

import pytest

import clients
import memory_local
import projects
import vault
from tools.memory import capture as cap
from tools.memory.capture import MemoryError, capture
from tools.memory.recall import recall
from tools.memory.recall_facts import recall_facts
from tools.memory.remember_fact import remember_fact


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    """No memory-core (every secret empty), a fresh store per test, and no CLAUDE_PROJECT_DIR from the test runner."""
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "sub" / "memory.db"))
    monkeypatch.setattr(vault, "secret", lambda name, default=None: None)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return tmp_path / "sub" / "memory.db"


# --- the store --------------------------------------------------------------------------------------------------------

def test_first_write_creates_the_folder_and_file(local_store):
    assert memory_local.count() == 0 and not local_store.exists()
    mid = memory_local.add("a", "s", "user", "hello")
    assert re.fullmatch(r"msg-[0-9a-f]{32}", mid) and local_store.exists()


def test_query_is_newest_first_in_memory_cores_shape():
    first = memory_local.add("a", "s", "user", "first")
    second = memory_local.add("a", "s", "assistant", "second")
    got = memory_local.query("a", "s", 10)
    assert [m["id"] for m in got] == [second, first]
    assert set(got[0]) == {"id", "role", "content", "timestamp"} and got[0]["role"] == "assistant"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", got[0]["timestamp"])


def test_query_keeps_to_one_agent_and_session_and_the_limit():
    for i in range(5):
        memory_local.add("a", "s", "user", f"a-s-{i}")
    memory_local.add("b", "s", "user", "other agent")
    memory_local.add("a", "t", "user", "other session")
    got = memory_local.query("a", "s", 3)
    assert [m["content"] for m in got] == ["a-s-4", "a-s-3", "a-s-2"]
    assert memory_local.query("a", "nothing", 5) == [] and memory_local.query("a", "s", 0) == []
    assert memory_local.count() == 7


def test_unicode_and_quotes_survive():
    text = "Alex's café ☕ — \"quoted\"; DROP TABLE messages; --"
    memory_local.add("a", "s", "user", text)
    assert memory_local.query("a", "s", 1)[0]["content"] == text and memory_local.count() == 1


# --- which agent ------------------------------------------------------------------------------------------------------

def test_without_memory_core_the_config_is_local():
    assert cap._config()["local"] is True


def test_owner_project_folder_gets_its_own_memory(monkeypatch):
    agent = cap._config(project_dir=r"D:\Work\Foo")["agent_id"]
    want = projects.norm(r"D:\Work\Foo")  # outside the f-string: a backslash inside one is a SyntaxError before 3.12
    assert agent == f"dir:{want}"
    assert cap._config(project_dir=r"D:\Work\Bar")["agent_id"] != agent
    assert cap._config()["agent_id"] == "shared"  # no folder at all
    assert cap._config("global", r"D:\Work\Foo")["agent_id"] == "shared"


def test_folder_from_the_header_or_claude_project_dir(monkeypatch):
    want = projects.norm(r"D:\Work\Foo")
    with projects.working_in(r"D:\Work\Foo"):
        assert cap._config()["agent_id"] == f"dir:{want}"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/home/me/app")
    assert cap._config()["agent_id"] == f"dir:{projects.norm('/home/me/app')}"


def test_a_registered_project_uses_its_agent(monkeypatch):
    monkeypatch.setattr(projects, "agent_for", lambda folder: "agt-registered" if folder else None)
    assert cap._config(project_dir=r"D:\Work\Foo")["agent_id"] == "agt-registered"


def test_a_client_never_reaches_a_folders_memory(monkeypatch):
    monkeypatch.setattr(clients, "memory_agent_for", lambda client: "agt-own" if client == "kit" else None)
    for client, expected in (("kit", "agt-own"), ("other", "app:other")):  # its own memory, never shared (2026-10-01)
        token = clients.current_client.set(client)
        try:
            assert cap._config(project_dir=r"D:\Work\Foo")["agent_id"] == expected
        finally:
            clients.current_client.reset(token)


def test_a_half_configured_memory_core_still_says_whats_missing(monkeypatch):
    monkeypatch.setattr(vault, "secret", lambda name, default=None: "http://memory-core:8420"
                        if name == "MEMORY_CORE_BASE_URL" else None)
    with pytest.raises(MemoryError, match="not configured.*MEMORY_CORE_BEARER"):
        cap._config()


# --- the tools, end to end --------------------------------------------------------------------------------------------

def test_capture_then_recall():
    out = capture("notes", "The deploy key lives in the vault", project_dir=r"D:\Work\Foo")
    assert out["accepted"] is True and out["message_id"].startswith("msg-")
    got = recall("notes", project_dir=r"D:\Work\Foo")
    assert got["total"] == 1 and got["messages"][0]["content"] == "The deploy key lives in the vault"
    assert recall("notes", project_dir=r"D:\Work\Bar")["total"] == 0  # another project's memory


def test_facts_tier_and_max_chars():
    remember_fact("x" * 500, category="platform", project_dir=r"D:\Work\Foo")
    got = recall_facts("platform", project_dir=r"D:\Work\Foo")
    assert got["total"] == 1 and got["messages"][0]["truncated"] is True and len(got["messages"][0]["content"]) <= 301


def test_recall_relevant_ranks_on_the_local_store(monkeypatch):
    from tools.memory import recall_relevant as rr
    monkeypatch.setattr(rr, "_semantic", lambda query, docs: None)  # no GPU service in tests
    remember_fact("The GPU service restarts with restart.ps1", category="platform", project_dir=r"D:\P")
    remember_fact("Promo videos live in D:\\data\\videos", category="platform", project_dir=r"D:\P")
    out = rr.recall_relevant("facts", "how do I restart the GPU service", project_dir=r"D:\P")
    assert out["messages"] and "restart.ps1" in out["messages"][0]["content"]


def test_a_broken_store_is_a_memory_error(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("MEMORY_DB_PATH", str(blocker / "memory.db"))
    with pytest.raises(MemoryError, match="built-in memory store"):
        capture("notes", "x")


# --- doctor -----------------------------------------------------------------------------------------------------------

def test_doctor_names_the_store(monkeypatch):
    import cli
    monkeypatch.delenv("MEMORY_CORE_BASE_URL", raising=False)
    memory_local.add("a", "s", "user", "one")
    state = cli.memory_state()
    assert state["state"] == "ready" and state["detail"].startswith("built-in, 1 saved")
    monkeypatch.setenv("MEMORY_CORE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(cli, "_get", lambda url: (False, None))
    assert cli.memory_state()["state"] == "broken"
