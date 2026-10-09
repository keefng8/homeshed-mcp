"""The Control Panel's own refreshes don't count as activity (the owner, 2026-10-02: 192 of the last 200 recent calls
were its repo.progress refresh, every 20 s, counted as reads). registry.quiet_call, set by server.py for the owner's
calls marked X-Homelab-Quiet, skips the activity log and usage for read tools only: anything that changes something,
any client token's call, and any refusal is always logged. Cases listed by local_ai.ask, checked."""
from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import registry


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(registry, "_REGISTRY", list(registry._REGISTRY))
    monkeypatch.setattr(registry, "_ACTIVITY_LOG", type(registry._ACTIVITY_LOG)(maxlen=200))
    recorded = []
    monkeypatch.setattr(registry.usage, "record", lambda *a, **k: recorded.append(a[0]))
    monkeypatch.setattr(registry.usage, "record_refusal", lambda *a, **k: recorded.append("refused"))
    return recorded


def _tool(name, risk, monkeypatch):
    @registry.tool(name=name, category="testonly", doc="fake.md")
    def run():
        return {"ok": True}
    real = registry.tool_meta
    monkeypatch.setattr(registry, "tool_meta", lambda tid: (risk, "testonly") if tid == f"testonly.{name}" else real(tid))
    return registry.get_capability(f"testonly.{name}").func


def _quietly(call):
    token = registry.quiet_call.set(True)
    try:
        return call()
    finally:
        registry.quiet_call.reset(token)


def test_a_quiet_read_is_served_but_not_logged(monkeypatch, _isolated):
    read = _tool("reads", "read", monkeypatch)
    assert _quietly(read) == {"ok": True}
    assert registry.get_activity_log() == [] and _isolated == []
    assert read() == {"ok": True}                                   # without the flag: logged as before
    assert [e["id"] for e in registry.get_activity_log()] == ["testonly.reads"] and _isolated == ["testonly.reads"]


def test_a_quiet_write_is_still_logged(monkeypatch, _isolated):
    _quietly(_tool("writes", "write", monkeypatch))
    assert [e["id"] for e in registry.get_activity_log()] == ["testonly.writes"]


def test_a_client_token_can_never_be_quiet(monkeypatch, _isolated):
    read = _tool("client_reads", "read", monkeypatch)
    monkeypatch.setattr(registry.clients, "check_call", lambda *a: (True, ""))
    token = registry.clients.current_client.set("some-app")
    try:
        _quietly(read)
    finally:
        registry.clients.current_client.reset(token)
    assert [e["client"] for e in registry.get_activity_log()] == ["some-app"]


def test_a_refusal_is_logged_even_when_quiet(monkeypatch, _isolated):
    read = _tool("switched_off", "read", monkeypatch)
    monkeypatch.setattr(registry.toolswitch, "why_disabled", lambda tid: "switched off by the owner")
    with pytest.raises(ToolError):
        _quietly(read)
    assert [e.get("refused") for e in registry.get_activity_log()] == [True] and _isolated == ["refused"]
