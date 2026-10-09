from __future__ import annotations

import registry


def test_finds_registered_capability_by_full_id(monkeypatch):
    def fake_func():
        return "ok"

    fake = registry.Capability(name="thing", category="fake", doc="fake.md", func=fake_func)
    monkeypatch.setattr(registry, "_REGISTRY", [fake])

    found = registry.get_capability("fake.thing")

    assert found is fake


def test_returns_none_for_unknown_id(monkeypatch):
    monkeypatch.setattr(registry, "_REGISTRY", [])

    assert registry.get_capability("nope.nothing") is None
