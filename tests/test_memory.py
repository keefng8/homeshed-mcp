"""Tests for memory.capture / memory.recall. No live memory-core required — httpx.post mocked."""
from unittest.mock import MagicMock, patch

import httpx
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


def test_capture_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "memory" and c.name == "capture"]
    assert len(matches) == 1


def test_recall_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "memory" and c.name == "recall"]
    assert len(matches) == 1


def test_capture_success():
    from tools.memory import capture as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 0, "data": {"accepted_ids": ["msg-abc123"]}}
    with patch("tools.memory.capture.httpx.post", return_value=fake_response) as mock_post:
        result = module.capture("proj-x", "the codename is Falcon")

    assert result == {"accepted": True, "message_id": "msg-abc123"}
    args, kwargs = mock_post.call_args
    assert args[0] == "http://localhost:8420/v3/conversation/add"
    assert kwargs["headers"]["Authorization"] == "Bearer local"
    assert kwargs["headers"]["x-tdai-service-id"] == "default"
    assert kwargs["headers"]["x-tdai-user-key"] == "sk-mem-test-key"
    body = kwargs["json"]
    assert body["team_id"] == "team-test"
    assert body["user_id"] == "usr-test"
    assert body["agent_id"] == "agt-test"
    assert body["session_id"] == "proj-x"
    assert body["messages"] == [{"role": "user", "content": "the codename is Falcon"}]


def test_capture_assistant_role():
    from tools.memory import capture as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 0, "data": {"accepted_ids": ["msg-1"]}}
    with patch("tools.memory.capture.httpx.post", return_value=fake_response) as mock_post:
        module.capture("proj-x", "OK", role="assistant")

    assert mock_post.call_args.kwargs["json"]["messages"] == [{"role": "assistant", "content": "OK"}]


def test_capture_empty_session_id_rejected():
    from tools.memory import capture as module

    with pytest.raises(module.MemoryError, match="session_id"):
        module.capture("", "hi")


def test_capture_empty_content_rejected():
    from tools.memory import capture as module

    with pytest.raises(module.MemoryError, match="content"):
        module.capture("proj-x", "")


def test_capture_content_too_long_rejected():
    from tools.memory import capture as module

    with pytest.raises(module.MemoryError, match="too long"):
        module.capture("proj-x", "x" * (module.MAX_CONTENT_CHARS + 1))


def test_capture_invalid_role_rejected():
    from tools.memory import capture as module

    with pytest.raises(module.MemoryError, match="role"):
        module.capture("proj-x", "hi", role="system")


def test_capture_missing_config(monkeypatch):
    from tools.memory import capture as module

    monkeypatch.delenv("MEMORY_TEAM_ID", raising=False)
    with pytest.raises(module.MemoryError, match="MEMORY_TEAM_ID"):
        module.capture("proj-x", "hi")


def test_capture_backend_unavailable():
    from tools.memory import capture as module

    with patch("tools.memory.capture.httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(module.MemoryError, match="unavailable"):
            module.capture("proj-x", "hi")


def test_capture_rejected_by_backend():
    from tools.memory import capture as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 400, "message": "bad identity"}
    with patch("tools.memory.capture.httpx.post", return_value=fake_response):
        with pytest.raises(module.MemoryError, match="bad identity"):
            module.capture("proj-x", "hi")


def test_capture_no_accepted_ids_marks_not_accepted():
    from tools.memory import capture as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 0, "data": {"accepted_ids": []}}
    with patch("tools.memory.capture.httpx.post", return_value=fake_response):
        result = module.capture("proj-x", "hi")

    assert result == {"accepted": False, "message_id": None}


def test_recall_success():
    from tools.memory import recall as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "code": 0,
        "data": {
            "messages": [
                {"id": "msg-1", "role": "user", "content": "hi", "timestamp": "2026-09-21T00:00:00Z"},
                {"id": "msg-2", "role": "assistant", "content": "OK", "timestamp": "2026-09-21T00:00:01Z"},
            ]
        },
    }
    with patch("tools.memory.recall.httpx.post", return_value=fake_response) as mock_post:
        result = module.recall("proj-x")

    assert result["total"] == 2
    assert result["messages"][0]["content"] == "hi"
    body = mock_post.call_args.kwargs["json"]
    assert body["session_id"] == "proj-x"
    assert body["limit"] == 20


def test_recall_empty_session_id_rejected():
    from tools.memory import recall as module

    with pytest.raises(module.MemoryError, match="session_id"):
        module.recall("")


def test_recall_invalid_limit_rejected():
    from tools.memory import recall as module

    with pytest.raises(module.MemoryError, match="limit"):
        module.recall("proj-x", limit=0)

    with pytest.raises(module.MemoryError, match="limit"):
        module.recall("proj-x", limit=101)


def test_recall_backend_timeout():
    from tools.memory import recall as module

    with patch("tools.memory.recall.httpx.post", side_effect=httpx.TimeoutException("timed out")):
        with pytest.raises(module.MemoryError, match="timed out"):
            module.recall("proj-x")


def test_recall_no_messages():
    from tools.memory import recall as module

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"code": 0, "data": {"messages": []}}
    with patch("tools.memory.recall.httpx.post", return_value=fake_response):
        result = module.recall("proj-x")

    assert result == {"messages": [], "total": 0}
