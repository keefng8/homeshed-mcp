"""memory.stats: reads and writes per app and category, an app sees only its own, the owner sees shares."""
import usage


def _calls(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.json")
    usage.record("memory.remember_fact", {"category": "agent-design-agent"}, {"accepted": True}, True, client="my-app")
    usage.record("memory.recall_facts", {"category": "agent-design-agent"}, {"facts": []}, True, client="my-app")
    usage.record("memory.recall_facts", {"category": "agent-design-agent"}, {}, False, client="my-app")  # failed: not counted
    usage.record("memory.recall", {}, {"messages": []}, True, client=None)
    usage.record("memory.capture", {}, {"ok": True}, True, client=None)
    usage.record("local_ai.ask", {}, {"text": "x"}, True, client="my-app")  # not memory


def test_an_app_sees_only_its_own_by_category(monkeypatch, tmp_path):
    _calls(monkeypatch, tmp_path)
    s = usage.memory_stats("my-app")
    assert (s["app"], s["reads"], s["writes"]) == ("my-app", 1, 1)
    assert s["categories"] == {"agent-design-agent": {"reads": 1, "writes": 1}}
    assert usage.memory_stats("someone-else")["reads"] == 0


def test_the_owner_sees_every_app_and_its_share(monkeypatch, tmp_path):
    _calls(monkeypatch, tmp_path)
    s = usage.memory_stats(None)
    assert (s["reads"], s["writes"]) == (2, 2)
    assert s["apps"]["my-app"] == {"reads": 1, "writes": 1, "share": 0.5}
    assert s["apps"]["owner"]["share"] == 0.5


def test_categories_are_capped(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(usage, "MAX_MEMORY_CATEGORIES", 2)
    for c in ("a", "b", "c"):
        usage.record("memory.remember_fact", {"category": c}, {}, True, client="my-app")
    s = usage.memory_stats("my-app")
    assert s["writes"] == 3 and sorted(s["categories"]) == ["a", "b"]


def test_the_tool_answers_for_the_caller(monkeypatch, tmp_path):
    import clients
    from tools.memory.stats import stats
    _calls(monkeypatch, tmp_path)
    token = clients.current_client.set("my-app")
    try:
        assert stats()["app"] == "my-app"
    finally:
        clients.current_client.reset(token)
    assert "apps" in stats()
