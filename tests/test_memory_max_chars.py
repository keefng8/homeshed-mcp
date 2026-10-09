"""max_chars on memory.recall / recall_facts / recall_relevant: the per-request memory check
(RULE-D-PROJECTS-010) stays cheap. 0 keeps today's full text."""
import pytest

from tools.memory import recall as recall_mod
from tools.memory.capture import MemoryError

LONG = "word " * 400  # 2,000 characters


class Resp:
    status_code = 200

    def __init__(self, messages):
        self._messages = messages

    def json(self):
        return {"code": 0, "data": {"messages": self._messages}}


@pytest.fixture(autouse=True)
def fake_memory(monkeypatch):
    msgs = [{"id": "1", "role": "user", "content": LONG + "platform facts", "timestamp": "t1"},
            {"id": "2", "role": "user", "content": "short platform note", "timestamp": "t2"}]
    monkeypatch.setattr(recall_mod, "_config", lambda scope="project", project_dir="": {"base_url": "http://m", "bearer": "b", "service_id": "s", "user_key": "u",
                                                       "team_id": "t", "user_id": "u", "agent_id": "a"})
    monkeypatch.setattr(recall_mod.httpx, "post", lambda *a, **k: Resp(msgs))


def call(fn, *args, **kw):
    return getattr(fn, "__wrapped__", fn)(*args, **kw)


def test_full_text_by_default():
    out = call(recall_mod.recall, "s1")
    assert out["messages"][0]["content"] == LONG + "platform facts" and "truncated" not in out["messages"][0]


def test_long_messages_are_cut_and_marked():
    out = call(recall_mod.recall, "s1", max_chars=300)
    first, second = out["messages"]
    assert len(first["content"]) <= 301 and first["content"].endswith("…") and first["truncated"] is True
    assert second == {"id": "2", "role": "user", "content": "short platform note", "timestamp": "t2"}  # short: untouched
    assert out["total"] == 2


@pytest.mark.parametrize("bad", [-1, 20001])
def test_out_of_range_is_refused(bad):
    with pytest.raises(MemoryError, match="max_chars"):
        call(recall_mod.recall, "s1", max_chars=bad)


def test_recall_facts_passes_it_through():
    from tools.memory import recall_facts as rf

    out = call(rf.recall_facts, "platform", limit=2, max_chars=100)
    assert out["messages"][0]["truncated"] is True and len(out["messages"][0]["content"]) <= 101


def test_the_fact_and_relevance_reads_default_to_300_characters_but_recall_keeps_full_text():
    """R&D's efficiency review (2026-09-29): recall_facts put out 1.87M characters in four days, 8.1K a call."""
    import inspect
    from tools.memory import recall_facts as rf, recall_relevant as rr

    def default(fn):
        return inspect.signature(fn).parameters["max_chars"].default

    assert default(rf.recall_facts) == default(rr.recall_relevant) == 300
    assert default(recall_mod.recall) == 0
    out = call(rf.recall_facts, "platform", limit=2)
    assert out["messages"][0]["truncated"] is True and len(out["messages"][0]["content"]) <= 301


def test_recall_relevant_ranks_on_full_text_then_cuts():
    from tools.memory import recall_relevant as rr

    out = call(rr.recall_relevant, "s1", "platform facts", max_results=1, max_chars=50)
    top = out["messages"][0]
    assert top["id"] == "1" and top["truncated"] is True and len(top["content"]) <= 51   # "facts" was near the end
    with pytest.raises(MemoryError, match="max_chars"):
        call(rr.recall_relevant, "s1", "x", max_chars=-5)
