"""Default-off through the real registry wrapper: the path MCP calls AND workflow.run take (workflow.run calls
registry.get_capability(...).func, which is the logged() wrapper)."""
import pytest
from mcp.server.mcpserver.exceptions import ToolError

import registry
import toolswitch


@pytest.fixture
def default_off_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(toolswitch, "SWITCH_FILE", tmp_path / "disabled_tools.json")
    monkeypatch.setattr(toolswitch, "_cache", (None, toolswitch._EMPTY))
    monkeypatch.setattr(toolswitch, "DEFAULT_OFF", toolswitch.DEFAULT_OFF | {"testwrite.danger"})
    monkeypatch.delenv("ENABLE_TOOLS", raising=False)
    monkeypatch.setattr(registry.usage, "record", lambda *a, **k: None)
    monkeypatch.setattr(registry.usage, "record_refusal", lambda *a, **k: None)
    before = list(registry._REGISTRY)

    @registry.tool(name="danger", category="testwrite", doc="x.md")
    def danger():
        return {"ran": True}

    yield registry.get_capability("testwrite.danger")
    registry._REGISTRY[:] = before


def test_refused_through_the_registry_until_enabled(default_off_tool, monkeypatch):
    with pytest.raises(ToolError, match="ENABLE_TOOLS=testwrite.danger"):
        default_off_tool.func()
    monkeypatch.setenv("ENABLE_TOOLS", "testwrite.*")
    assert default_off_tool.func() == {"ran": True}


def test_refusal_is_logged_as_a_refusal_not_a_tool_error(default_off_tool):
    with pytest.raises(ToolError):
        default_off_tool.func()
    last = registry._ACTIVITY_LOG[-1]
    assert last["id"] == "testwrite.danger" and last["refused"] is True
