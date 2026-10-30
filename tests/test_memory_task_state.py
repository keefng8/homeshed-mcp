"""Task-state facts (R&D, 2026-10-30): the newest fact per task name hides the older ones on recall, and each one
carries verified / planned. httpx is mocked; nothing is written anywhere."""
from unittest.mock import MagicMock, patch

import pytest

from tools.memory.task_state import current_only, label, tagged


@pytest.fixture(autouse=True)
def memory_env(monkeypatch):
    monkeypatch.setenv("MEMORY_CORE_BASE_URL", "http://localhost:8420")
    monkeypatch.setenv("MEMORY_CORE_BEARER", "local")
    monkeypatch.setenv("MEMORY_SERVICE_ID", "default")
    monkeypatch.setenv("MEMORY_USER_KEY", "sk-mem-test-key")
    monkeypatch.setenv("MEMORY_TEAM_ID", "team-test")
    monkeypatch.setenv("MEMORY_USER_ID", "usr-test")
    monkeypatch.setenv("MEMORY_AGENT_ID", "agt-test")


def fact(i, content, ts):
    return {"id": f"msg-{i}", "role": "user", "content": content, "timestamp": ts}


def test_tagged_adds_the_prefix_and_refuses_bad_input():
    assert tagged("x", "", "") == "x"
    assert tagged("seat claims", "storyeditor seat", "Verified") == "TASK[storyeditor seat] verified: seat claims"
    assert tagged("x", "release 0.7.2", "") == "TASK[release 0.7.2]: x"
    for task, state in (("", "planned"), ("a]b", ""), ("ok", "done"), ("x" * 61, "")):
        with pytest.raises(ValueError):
            tagged("x", task, state)


def test_the_newest_fact_per_task_wins_and_others_stay():
    msgs = [
        fact(1, "TASK[seat] planned: waiting on the owner", "2026-10-29T22:00:00Z"),
        fact(2, "an untagged fact", "2026-10-29T21:00:00Z"),
        fact(3, "TASK[Seat] verified: claims tasks", "2026-10-29T23:30:00Z"),
        fact(4, "TASK[other]: hold", "2026-10-29T20:00:00Z"),
    ]
    out = current_only(msgs)
    assert [m["id"] for m in out] == ["msg-2", "msg-3", "msg-4"]
    assert out[1]["state"] == "verified" and out[1]["task"] == "Seat"
    assert out[2]["state"] == "" and "task" not in out[0]


def test_without_timestamps_the_first_in_newest_first_order_wins():
    msgs = [{"id": "a", "content": "TASK[t] verified: new"}, {"id": "b", "content": "TASK[t] planned: old"}]
    assert [m["id"] for m in current_only(msgs)] == ["a"]
    assert label({"content": "TASK[t]x"}) == {"content": "TASK[t]x"}  # not a tag: no ": " after it


def test_remember_fact_stores_the_tag_and_rejects_a_bad_state():
    from tools.memory import remember_fact as module
    from tools.memory.capture import MemoryError

    ok = MagicMock(status_code=200)
    ok.json.return_value = {"code": 0, "data": {"accepted_ids": ["msg-1"]}}
    with patch("tools.memory.capture.httpx.post", return_value=ok) as post:
        module.remember_fact("claims tasks", category="tasks", task="seat", state="verified")
    assert post.call_args_list[0].kwargs["json"]["messages"][0]["content"] == "TASK[seat] verified: claims tasks"
    with patch("tools.memory.capture.httpx.post", return_value=ok) as post, pytest.raises(MemoryError):
        module.remember_fact("x", category="tasks", task="seat", state="maybe")
    post.assert_not_called()


def test_recall_facts_hides_the_older_state():
    from tools.memory import recall_facts as module

    got = {"messages": [fact(2, "TASK[seat] verified: done", "2026-10-30T01:00:00Z"),
                        fact(1, "TASK[seat] planned: waiting", "2026-10-29T01:00:00Z")], "total": 2}
    with patch.object(module, "recall", return_value=got):
        out = module.recall_facts(category="tasks")
    assert [m["id"] for m in out["messages"]] == ["msg-2"] and out["total"] == 2
    assert out["messages"][0]["state"] == "verified"
