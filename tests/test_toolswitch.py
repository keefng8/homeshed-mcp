"""toolswitch (per-tool on/off) + registry refusal + /tools routes. Case list drafted by local_ai.ask
(Qwen3-Coder-30B)."""
import asyncio
import importlib
import json
import sys

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import registry
import toolswitch


@pytest.fixture(autouse=True)
def switch_file(tmp_path, monkeypatch):
    f = tmp_path / "disabled_tools.json"
    monkeypatch.setattr(toolswitch, "SWITCH_FILE", f)
    monkeypatch.setattr(toolswitch, "_cache", (None, frozenset()))
    return f


def test_missing_file_means_everything_is_on():
    assert toolswitch.disabled() == [] and not toolswitch.is_disabled("memory.recall")


def test_switch_off_then_on_reports_changes_and_persists(switch_file):
    assert toolswitch.set_enabled("docker.container.stop", False) is True
    assert toolswitch.is_disabled("docker.container.stop")
    assert json.loads(switch_file.read_text())["disabled"] == ["docker.container.stop"]
    assert toolswitch.set_enabled("docker.container.stop", False) is False  # already off
    assert toolswitch.set_enabled("docker.container.stop", True) is True
    assert toolswitch.set_enabled("docker.container.stop", True) is False  # already on
    assert toolswitch.disabled() == []


def test_disabled_is_sorted():
    for t in ("z.tool", "a.tool", "m.tool"):
        toolswitch.set_enabled(t, False)
    assert toolswitch.disabled() == ["a.tool", "m.tool", "z.tool"]


def test_corrupt_file_fails_open(switch_file, caplog):
    switch_file.write_text("{not json")
    assert toolswitch.disabled() == [] and not toolswitch.is_disabled("anything")
    assert "fail open" in caplog.text


def test_write_is_atomic_no_temp_left(switch_file):
    toolswitch.set_enabled("x.y", False)
    assert switch_file.exists() and not switch_file.with_suffix(".tmp").exists()


def test_external_edit_is_picked_up_without_restart(switch_file):
    assert not toolswitch.is_disabled("a.b")
    switch_file.write_text(json.dumps({"disabled": ["a.b"]}))  # e.g. edited by hand
    assert toolswitch.is_disabled("a.b")


@pytest.fixture
def dummy_tool():
    @registry.tool(name="dummy", category="testswitch", doc="x.md")
    def dummy():
        return {"ok": True}

    cap = registry.get_capability("testswitch.dummy")
    yield cap
    registry._REGISTRY.remove(cap)


def test_switched_off_tool_is_refused_and_logged_as_refusal(dummy_tool, monkeypatch):
    recorded = []
    monkeypatch.setattr(registry.usage, "record", lambda *a, **k: recorded.append(a))
    assert dummy_tool.func() == {"ok": True}
    toolswitch.set_enabled("testswitch.dummy", False)
    with pytest.raises(ToolError, match="switched off by the owner"):
        dummy_tool.func()
    last = registry.get_activity_log()[0]
    assert last["id"] == "testswitch.dummy" and last["refused"] is True and last["ok"] is False
    assert len(recorded) == 1  # only the real call reached usage accounting, not the refusal


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    return importlib.import_module("server")


class Req:
    def __init__(self, path=None):
        self.path_params = path or {}
        self.query_params = {}


def test_switch_route_validates_and_toggles(server):
    ok = asyncio.run(server.tools_switch(Req({"tool_id": "memory.recall_facts", "action": "disable"})))
    assert json.loads(ok.body) == {"id": "memory.recall_facts", "enabled": False, "changed": True}
    assert asyncio.run(server.tools_switch(Req({"tool_id": "nope.tool", "action": "disable"}))).status_code == 404
    assert asyncio.run(server.tools_switch(Req({"tool_id": "memory.recall_facts", "action": "explode"}))).status_code == 400
    asyncio.run(server.tools_switch(Req({"tool_id": "memory.recall_facts", "action": "enable"})))
    assert not toolswitch.is_disabled("memory.recall_facts")


def test_status_route_lists_every_tool_with_scope_and_switch_state(server, monkeypatch):
    monkeypatch.delenv("ENABLE_TOOLS", raising=False)
    toolswitch.set_enabled("memory.recall_facts", False)
    body = json.loads(asyncio.run(server.tools_status(Req())).body)
    rows = {t["id"]: t for t in body["tools"]}
    assert len(rows) >= 50 and "memory.recall_facts" in body["switched_off"]
    assert rows["memory.recall_facts"]["enabled"] is False and "by the owner" in rows["memory.recall_facts"]["off_reason"]
    # A tool that ships off shows as off, with how to turn it on: never "on" while every call is refused.
    assert rows["docker.container.stop"]["enabled"] is False and "ENABLE_TOOLS" in rows["docker.container.stop"]["off_reason"]
    assert set(body["switched_off"]) >= toolswitch.DEFAULT_OFF & set(rows)
    assert rows["memory.recall"]["enabled"] is True and rows["memory.recall"]["off_reason"] is None
    assert rows["docker.container.stop"]["scope"] == "write:docker" and rows["memory.recall"]["scope"] == "read:memory"
    assert {"calls", "errors", "success_rate", "avg_ms", "last_call"} <= set(rows["memory.recall"])
    monkeypatch.setenv("ENABLE_TOOLS", "docker.*")
    body = json.loads(asyncio.run(server.tools_status(Req())).body)
    assert "docker.container.stop" not in body["switched_off"] and "memory.recall_facts" in body["switched_off"]


def test_healthz_is_cheap_and_ok(server):
    assert json.loads(asyncio.run(server.healthz(Req())).body) == {"status": "ok"}
