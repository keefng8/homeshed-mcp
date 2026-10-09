"""Tests for registry.find. First drafted by local_ai.ask (thinking disabled, 15.9s, then
corrected — real bugs found: wrong error message, ask() mocked as a string instead of the dict
it actually returns, an off-by-one match count, missing import). manifest.py and local_ai.ask
are both mocked — no live daemon/GPU required.
"""
from unittest.mock import patch

import pytest

MANIFESTS = [
    {"id": "test.one", "name": "Test One", "description": "First test capability", "aliases": ["test", "t1"]},
    {"id": "test.two", "name": "Test Two", "description": "Second test capability", "aliases": ["another", "t2"]},
]


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "registry" and c.name == "find"]
    assert len(matches) == 1


def test_empty_query_rejected():
    from tools.registry.find import find

    with pytest.raises(ValueError):
        find("")
    with pytest.raises(ValueError):
        find("   ")


def test_exact_id_match():
    from tools.registry.find import find

    with patch("tools.registry.find.load_manifests", return_value=MANIFESTS):
        result = find("test.one")

    assert result == {
        "tier": "exact_id",
        "matches": [{"id": "test.one", "name": "Test One", "description": "First test capability"}],
    }


def test_alias_keyword_match():
    from tools.registry.find import find

    with patch("tools.registry.find.load_manifests", return_value=MANIFESTS):
        result = find("t1")

    assert result["tier"] == "alias_keyword"
    assert [m["id"] for m in result["matches"]] == ["test.one"]


def test_alias_keyword_respects_top_n():
    from tools.registry.find import find

    broad = MANIFESTS + [{"id": "test.three", "name": "Test Three", "description": "d", "aliases": ["test"]}]
    with patch("tools.registry.find.load_manifests", return_value=broad):
        result = find("test", top_n=2)

    assert result["tier"] == "alias_keyword"
    assert len(result["matches"]) == 2


def test_local_ai_fallback_match():
    from tools.registry.find import find

    fake_ask_result = {"text": "test.two", "model": "m", "finish_reason": "stop"}
    with patch("tools.registry.find.load_manifests", return_value=MANIFESTS):
        with patch("tools.registry.find.ask", return_value=fake_ask_result) as mock_ask:
            result = find("something with no keyword overlap at all")

    assert result == {
        "tier": "local_ai_fallback",
        "matches": [{"id": "test.two", "name": "Test Two", "description": "Second test capability"}],
    }
    mock_ask.assert_called_once()


def test_local_ai_fallback_no_match():
    from tools.registry.find import find

    fake_ask_result = {"text": "none", "model": "m", "finish_reason": "stop"}
    with patch("tools.registry.find.load_manifests", return_value=MANIFESTS):
        with patch("tools.registry.find.ask", return_value=fake_ask_result):
            result = find("something with no keyword overlap at all")

    assert result == {"tier": "no_match", "matches": []}


def test_local_ai_backend_failure_falls_through_to_no_match():
    from tools.registry.find import find
    from tools.local_ai.ask import LocalAIError

    with patch("tools.registry.find.load_manifests", return_value=MANIFESTS):
        with patch("tools.registry.find.ask", side_effect=LocalAIError("unavailable")):
            result = find("something with no keyword overlap at all")

    assert result == {"tier": "no_match", "matches": []}
