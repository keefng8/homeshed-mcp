"""/memory/facts (2026-10-01): the optional panel reads the shared memory through HomeShed when it has no memory-core
address of its own, so a new install's built-in memory shows (the prepper's clean-VM test). Read straight from the
store: the panel's polling never counts as tool calls. Case list drafted by local_ai.ask (Qwen3-Coder-30B)."""
import asyncio
import importlib
import json
import sys

import pytest

import registry
import vault


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setattr(vault, "secret", lambda name, default=None: None)  # no memory-core: the built-in store
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    sys.modules.pop("server", None)
    return importlib.import_module("server")


class Req:
    def __init__(self, query=None):
        self.query_params = query or {}


def _get(server, **query):
    resp = asyncio.run(server.memory_facts_route(Req({k: str(v) for k, v in query.items()})))
    return resp.status_code, json.loads(resp.body)


def test_reads_a_category_newest_first_and_is_never_a_tool_call(server):
    from tools.memory.remember_fact import remember_fact
    remember_fact("first platform fact", category="platform")
    remember_fact("second platform fact", category="platform")
    remember_fact("a task", category="tasks")
    before = len(registry.get_activity_log())
    status, body = _get(server, category="platform", limit=10)
    assert status == 200 and [m["content"] for m in body["messages"]] == ["second platform fact", "first platform fact"]
    assert len(registry.get_activity_log()) == before  # the panel's polling isn't activity


def test_limits_are_clamped_and_text_is_cut(server):
    from tools.memory.remember_fact import remember_fact
    for i in range(3):
        remember_fact("x" * 50 + str(i), category="general")
    status, body = _get(server, category="general", limit=500, max_chars=10)
    assert status == 200 and len(body["messages"]) == 3 and body["messages"][0]["content"].endswith("…")
    assert _get(server, category="general", limit=0)[1]["total"] == 1  # at least one


@pytest.mark.parametrize("query", [{"category": "../etc"}, {"category": "Platform"}, {"limit": "many"}])
def test_bad_queries_are_refused(server, query):
    assert _get(server, **query)[0] == 400


def test_a_store_that_fails_is_a_502(server, monkeypatch):
    from tools.memory.capture import MemoryError

    def broken(*_a):
        raise MemoryError("memory-core is unavailable")

    monkeypatch.setattr(server, "_panel_facts", broken)
    status, body = _get(server, category="platform")
    assert status == 502 and body["error"] == "memory-core is unavailable"
