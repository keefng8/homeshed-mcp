"""Real bug found live 2026-09-23: the MCP SDK's Tool.run() strips a bare exception's message
before it reaches the client, treating it as an unanticipated crash — every capability's own
documented "Raises: ValueError if..." was silently losing its message this whole time. None of
this project's other tests would ever have caught this: they all call the raw unwrapped function
directly (see registry.py's own tool() docstring), never the logged() wrapper server.py actually
serves. These tests exercise logged() itself.
"""
from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import registry


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """@registry.tool() appends to the real, shared _REGISTRY list -- every test below defines
    a fresh fake capability with it, so isolate that list per test rather than letting each
    registration leak into every later test in the suite (nothing currently asserts on registry
    size, but there's no reason to leave stray "testonly.*" entries lying around either)."""
    monkeypatch.setattr(registry, "_REGISTRY", list(registry._REGISTRY))


def test_logged_wrapper_preserves_the_original_error_message_as_tool_error():
    @registry.tool(name="thing", category="testonly", doc="fake.md")
    def raises_a_clear_message():
        raise ValueError("a very specific, deliberate reason")

    capability = registry.get_capability("testonly.thing")
    assert capability is not None

    with pytest.raises(ToolError, match="a very specific, deliberate reason"):
        capability.func()


def test_logged_wrapper_still_records_activity_log_entry_on_failure():
    @registry.tool(name="thing2", category="testonly", doc="fake.md")
    def raises():
        raise RuntimeError("boom")

    capability = registry.get_capability("testonly.thing2")
    with pytest.raises(ToolError):
        capability.func()

    entries = [e for e in registry.get_activity_log() if e["id"] == "testonly.thing2"]
    assert entries and entries[0]["ok"] is False


def test_logged_wrapper_passes_through_successful_result_unchanged():
    @registry.tool(name="thing3", category="testonly", doc="fake.md")
    def succeeds(x):
        return {"doubled": x * 2}

    capability = registry.get_capability("testonly.thing3")
    assert capability.func(5) == {"doubled": 10}


def test_original_unwrapped_function_still_raises_its_own_exception_type():
    """The decorator returns the ORIGINAL func (unlogged, unwrapped) so every other test file's
    `from tools.x.y import y` pattern keeps working exactly as before this fix -- confirm that
    contract explicitly, since it's what every other test in this suite silently depends on."""

    @registry.tool(name="thing4", category="testonly", doc="fake.md")
    def raises_value_error():
        raise ValueError("still a plain ValueError when imported directly")

    with pytest.raises(ValueError, match="still a plain ValueError"):
        raises_value_error()
