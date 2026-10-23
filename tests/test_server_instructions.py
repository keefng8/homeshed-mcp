"""The MCP instructions a client gets on connect (2026-10-22): the tool groups this install really has, and
registry.find as the way to pick a tool, short enough to send every session."""
import registry
import server


def test_instructions_name_this_installs_groups_and_registry_find():
    text = server._instructions()
    assert "registry.find" in text and len(text) < 1500
    for group in {c.category for c in registry.discover()}:
        assert group in text


def test_instructions_survive_a_broken_registry(monkeypatch):
    monkeypatch.setattr(registry, "discover", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    text = server._instructions()
    assert text.startswith("HomeShed:") and "registry.find" in text
