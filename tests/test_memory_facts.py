"""Tests for memory.remember_fact / memory.recall_facts — thin wrappers around memory.capture/
memory.recall hardcoded to the facts-tier session_id, now category-scoped (2026-09-21). httpx.post
is mocked throughout.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def memory_env(monkeypatch):
    monkeypatch.setenv("MEMORY_CORE_BASE_URL", "http://localhost:8420")
    monkeypatch.setenv("MEMORY_CORE_BEARER", "local")
    monkeypatch.setenv("MEMORY_SERVICE_ID", "default")
    monkeypatch.setenv("MEMORY_USER_KEY", "sk-mem-test-key")
    monkeypatch.setenv("MEMORY_TEAM_ID", "team-test")
    monkeypatch.setenv("MEMORY_USER_ID", "usr-test")
    monkeypatch.setenv("MEMORY_AGENT_ID", "agt-test")


def test_remember_fact_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "memory" and c.name == "remember_fact"]
    assert len(matches) == 1


def test_recall_facts_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "memory" and c.name == "recall_facts"]
    assert len(matches) == 1


def test_remember_fact_default_category_uses_bare_facts_session():
    from tools.memory import remember_fact as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 0, "data": {"accepted_ids": ["msg-1"]}}
    with patch("tools.memory.capture.httpx.post", return_value=fake_response) as mock_post:
        result = module.remember_fact("the platform is not app-specific")

    assert result == {"accepted": True, "message_id": "msg-1"}
    body = mock_post.call_args.kwargs["json"]
    assert body["session_id"] == "facts"
    assert body["messages"] == [{"role": "user", "content": "the platform is not app-specific"}]


def test_remember_fact_custom_category_gets_own_session():
    from tools.memory import remember_fact as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 0, "data": {"accepted_ids": ["msg-1"]}}
    with patch("tools.memory.capture.httpx.post", return_value=fake_response) as mock_post:
        module.remember_fact("qwen-coder is the default local model", category="local-ai")

    assert mock_post.call_args.kwargs["json"]["session_id"] == "facts:local-ai"


def test_remember_fact_invalid_category_rejected():
    from tools.memory import remember_fact as module
    from tools.memory.capture import MemoryError

    with pytest.raises(MemoryError, match="category"):
        module.remember_fact("x", category="Not Valid!")


def test_recall_facts_default_category_uses_bare_facts_session_and_smaller_limit():
    from tools.memory import recall_facts as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 0, "data": {"messages": []}}
    with patch("tools.memory.recall.httpx.post", return_value=fake_response) as mock_post:
        module.recall_facts()

    body = mock_post.call_args.kwargs["json"]
    assert body["session_id"] == "facts"
    assert body["limit"] == 20


def test_recall_facts_custom_category_reads_its_own_session_only():
    from tools.memory import recall_facts as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 0, "data": {"messages": []}}
    with patch("tools.memory.recall.httpx.post", return_value=fake_response) as mock_post:
        module.recall_facts(category="mcp-server")

    assert mock_post.call_args.kwargs["json"]["session_id"] == "facts:mcp-server"


def test_recall_facts_respects_explicit_limit():
    from tools.memory import recall_facts as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 0, "data": {"messages": []}}
    with patch("tools.memory.recall.httpx.post", return_value=fake_response) as mock_post:
        module.recall_facts(limit=5)

    assert mock_post.call_args.kwargs["json"]["limit"] == 5


def test_recall_facts_invalid_category_rejected():
    from tools.memory import recall_facts as module
    from tools.memory.capture import MemoryError

    with pytest.raises(MemoryError, match="category"):
        module.recall_facts(category="")


def test_remember_fact_propagates_capture_errors():
    from tools.memory import remember_fact as module
    from tools.memory.capture import MemoryError

    with pytest.raises(MemoryError, match="content"):
        module.remember_fact("")


def test_recall_facts_propagates_recall_errors():
    from tools.memory import recall_facts as module
    from tools.memory.capture import MemoryError

    with pytest.raises(MemoryError, match="limit"):
        module.recall_facts(limit=0)



@pytest.fixture(autouse=True)
def _no_category_index_unless_asked(monkeypatch, request):
    """The remember_fact tests above check the fact's own write; indexing its category is tested below."""
    if "index" not in request.node.name:
        from tools.memory import remember_fact as module
        monkeypatch.setattr(module, "_index", lambda category, scope="project": None)


def test_a_new_category_is_indexed_once_and_a_failing_index_never_loses_the_fact(monkeypatch):
    from tools.memory import remember_fact as module
    import tools.memory.recall_relevant as rr
    written = []
    monkeypatch.setattr(module, "capture", lambda session_id, content, role="user", scope="project":
                        written.append((session_id, content)) or {"accepted": True})
    monkeypatch.setattr(rr, "fact_categories", lambda scope="project": ["general", "security"])
    module.remember_fact("a", category="security")
    module.remember_fact("b", category="video-studio")
    assert written == [("facts:security", "a"), ("facts:video-studio", "b"), ("facts:fact-index", "video-studio")]
    monkeypatch.setattr(rr, "fact_categories", lambda scope="project": (_ for _ in ()).throw(RuntimeError("down")))
    assert module.remember_fact("c", category="another-index-test")["accepted"]
