"""Tests for notify.send. httpx mocked throughout -- no live ntfy backend needed. A real publish
to a throwaway topic was confirmed working live 2026-09-23 (recorded in
capabilities/notify/send.md), not re-asserted against real infrastructure on every test run.
"""
from unittest.mock import MagicMock, patch

import httpx
import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "notify" and c.name == "send"]
    assert len(matches) == 1


@pytest.fixture(autouse=True)
def ntfy_env(monkeypatch):
    monkeypatch.setenv("NTFY_BASE_URL", "http://192.168.1.10:8080")
    monkeypatch.setenv("NTFY_AUTH_TOKEN", "tk_test_token")
    from tools.notify import send as module
    for state in (module._sent, module._seen, module._held):  # bundling is per process: each test starts clean
        state.clear()


def _ntfy_response(status_code=200, id_="VKZLiaXDmW0s"):
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = {
        "id": id_, "time": 1790162211, "expires": 1790205411,
        "event": "message", "topic": "mcp-server", "message": "hello",
    }
    return resp


def test_happy_path_default_topic():
    from tools.notify import send as module

    with patch("tools.notify.send.httpx.post", return_value=_ntfy_response()) as mock_post:
        result = module.send("deploy finished")

    assert result == {"sent": True, "topic": "mcp-server", "id": "VKZLiaXDmW0s", "message": "deploy finished"}
    assert mock_post.call_args.args[0] == "http://192.168.1.10:8080/mcp-server"
    assert mock_post.call_args.kwargs["content"] == b"deploy finished"
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer tk_test_token"


def test_custom_topic_title_priority_tags():
    from tools.notify import send as module

    with patch("tools.notify.send.httpx.post", return_value=_ntfy_response()) as mock_post:
        module.send(
            "disk almost full",
            topic="alerts",
            title="Storage warning",
            priority="urgent",
            tags=["warning", "floppy_disk"],
        )

    assert mock_post.call_args.args[0] == "http://192.168.1.10:8080/alerts"
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Title"] == "Storage warning"
    assert headers["Priority"] == "urgent"
    assert headers["Tags"] == "warning,floppy_disk"


def test_empty_message_raises_before_any_request():
    from tools.notify import send as module

    with patch("tools.notify.send.httpx.post") as mock_post:
        with pytest.raises(module.NotifyError, match="message must be non-empty"):
            module.send("")
    mock_post.assert_not_called()


def test_invalid_priority_raises_before_any_request():
    from tools.notify import send as module

    with patch("tools.notify.send.httpx.post") as mock_post:
        with pytest.raises(module.NotifyError, match="priority must be one of"):
            module.send("hi", priority="super-urgent")
    mock_post.assert_not_called()


def test_missing_base_url_raises_clear_error(monkeypatch):
    monkeypatch.delenv("NTFY_BASE_URL", raising=False)
    from tools.notify import send as module

    with pytest.raises(module.NotifyError, match="NTFY_BASE_URL"):
        module.send("hi")


def test_missing_auth_token_raises_clear_error(monkeypatch):
    monkeypatch.delenv("NTFY_AUTH_TOKEN", raising=False)
    from tools.notify import send as module

    with pytest.raises(module.NotifyError, match="NTFY_AUTH_TOKEN"):
        module.send("hi")


def test_unreachable_backend_raises_not_crashes():
    from tools.notify import send as module

    with patch("tools.notify.send.httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(module.NotifyError, match="could not reach"):
            module.send("hi")


def test_non_200_raises():
    from tools.notify import send as module

    with patch("tools.notify.send.httpx.post", return_value=_ntfy_response(status_code=500)):
        with pytest.raises(module.NotifyError, match="500"):
            module.send("hi")


# Default topic setting (2026-09-28). Case shapes drafted by local_ai.ask (qwen-coder); it called
# the module instead of module.send and guessed the request args, so those were fixed by hand.
def test_default_topic_from_setting(monkeypatch):
    from tools.notify import send as module

    monkeypatch.setenv("NTFY_DEFAULT_TOPIC", "Alerts")
    with patch("tools.notify.send.httpx.post", return_value=_ntfy_response()) as mock_post:
        result = module.send("hi")
    assert result["topic"] == "Alerts"
    assert mock_post.call_args.args[0] == "http://192.168.1.10:8080/Alerts"


def test_explicit_topic_beats_setting(monkeypatch):
    from tools.notify import send as module

    monkeypatch.setenv("NTFY_DEFAULT_TOPIC", "Alerts")
    with patch("tools.notify.send.httpx.post", return_value=_ntfy_response()) as mock_post:
        result = module.send("hi", topic="other")
    assert result["topic"] == "other" and mock_post.call_args.args[0].endswith("/other")


# Bundling (2026-10-07): only what matters buzzes the phone.
def test_a_repeat_within_30_minutes_is_dropped_but_a_failed_send_can_retry():
    from tools.notify import send as module

    with patch("tools.notify.send.httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(module.NotifyError):
            module.send("order on hold", title="Printify")
    with patch("tools.notify.send.httpx.post", return_value=_ntfy_response()) as mock_post:
        assert module.send("order on hold", title="Printify")["sent"] is True  # the failure didn't count
        again = module.send("order on hold", title="Printify")
    assert again["sent"] is False and "30 minutes" in again["dropped"] and mock_post.call_count == 1


def test_a_burst_is_held_and_goes_as_one_summary_while_urgent_always_goes(monkeypatch):
    from tools.notify import send as module

    timers = []
    monkeypatch.setattr(module.threading, "Timer", lambda wait, fn, args: timers.append((wait, fn, args)) or MagicMock())
    with patch("tools.notify.send.httpx.post", return_value=_ntfy_response()) as mock_post:
        for n in range(3):
            assert module.send(f"update {n}", title="Shop")["sent"] is True
        held = [module.send(f"update {n}", title="Shop") for n in (3, 4)]
        assert all(h["held"] for h in held) and len(timers) == 1 and 0 < timers[0][0] <= module.WINDOW_S
        assert module.send("loss limit hit", priority="urgent")["sent"] is True  # never held
        assert mock_post.call_count == 4
        wait, fn, args = timers[0]
        fn(*args)  # the window closes
    summary = mock_post.call_args
    assert summary.kwargs["headers"]["Title"] == "2 more updates"
    assert summary.kwargs["content"].decode() == "- Shop: update 3\n- Shop: update 4"
    assert module._held == {}
