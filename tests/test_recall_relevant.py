from __future__ import annotations

import pytest

import tools.memory.recall_relevant as recall_relevant_module
from tools.memory.recall_relevant import recall_relevant


def _fake_recall(messages):
    return lambda session_id, limit=20, scope="project": {"messages": messages, "total": len(messages)}


def test_ranks_by_word_overlap_descending(monkeypatch):
    messages = [
        {"id": "1", "role": "user", "content": "the weather is nice today", "timestamp": "t1"},
        {"id": "2", "role": "user", "content": "deploy the web service to production", "timestamp": "t2"},
        {"id": "3", "role": "user", "content": "deploy service and verify it is healthy", "timestamp": "t3"},
    ]
    monkeypatch.setattr(recall_relevant_module, "recall", _fake_recall(messages))

    out = recall_relevant("s1", "deploy the service", max_results=3)

    scores = [m["relevance_score"] for m in out["messages"]]
    assert scores == sorted(scores, reverse=True)
    assert out["messages"][0]["id"] in ("2", "3")  # both share "deploy"/"service" with the query
    assert out["messages"][-1]["id"] == "1"  # least overlap, correctly ranked last
    assert out["total_considered"] == 3


def test_max_results_truncates(monkeypatch):
    messages = [{"id": str(i), "role": "user", "content": "deploy service", "timestamp": f"t{i}"} for i in range(10)]
    monkeypatch.setattr(recall_relevant_module, "recall", _fake_recall(messages))

    out = recall_relevant("s1", "deploy", max_results=3)

    assert len(out["messages"]) == 3
    assert out["total_considered"] == 10


def test_no_overlap_still_returns_results_with_zero_score(monkeypatch):
    messages = [{"id": "1", "role": "user", "content": "completely unrelated content", "timestamp": "t1"}]
    monkeypatch.setattr(recall_relevant_module, "recall", _fake_recall(messages))

    out = recall_relevant("s1", "deploy service", max_results=5)

    assert out["messages"][0]["relevance_score"] == 0


def test_empty_query_raises():
    with pytest.raises(ValueError, match="non-empty"):
        recall_relevant("s1", "")


def test_whitespace_query_raises():
    with pytest.raises(ValueError, match="non-empty"):
        recall_relevant("s1", "   ")


def test_max_results_out_of_range_raises():
    with pytest.raises(ValueError, match="max_results"):
        recall_relevant("s1", "deploy", max_results=0)
    with pytest.raises(ValueError, match="max_results"):
        recall_relevant("s1", "deploy", max_results=51)


def test_case_insensitive_matching(monkeypatch):
    messages = [{"id": "1", "role": "user", "content": "DEPLOY THE SERVICE", "timestamp": "t1"}]
    monkeypatch.setattr(recall_relevant_module, "recall", _fake_recall(messages))

    out = recall_relevant("s1", "deploy service", max_results=1)
    lower = [{**messages[0], "content": "deploy the service"}]
    monkeypatch.setattr(recall_relevant_module, "recall", _fake_recall(lower))
    same = recall_relevant("s1", "deploy service", max_results=1)

    # BM25 scores are fractional (2026-09-29): the point is that case changes nothing, and it matched
    assert out["messages"][0]["relevance_score"] == same["messages"][0]["relevance_score"] > 0


def test_forwards_limit_to_recall(monkeypatch):
    captured = {}

    def fake_recall(session_id, limit=20, scope="project"):
        captured["session_id"] = session_id
        captured["limit"] = limit
        return {"messages": [], "total": 0}

    monkeypatch.setattr(recall_relevant_module, "recall", fake_recall)

    recall_relevant("my-session", "query", limit=100, max_results=5)

    assert captured == {"session_id": "my-session", "limit": 100}


def test_empty_message_history_returns_empty_results(monkeypatch):
    monkeypatch.setattr(recall_relevant_module, "recall", _fake_recall([]))

    out = recall_relevant("s1", "anything")

    assert out["messages"] == []
    assert out["total_considered"] == 0


@pytest.fixture(autouse=True)
def _no_fact_categories_unless_asked(monkeypatch, request):
    """The ranking tests above are about one session's messages; the facts tests below opt back in."""
    if "facts" not in request.node.name:
        monkeypatch.setattr(recall_relevant_module, "_facts", lambda limit, scope: [])


def _by_session(table):
    def fake(session_id, limit=50, scope="project", max_chars=0):
        return {"messages": list(table.get(session_id, [])), "total": len(table.get(session_id, []))}
    return fake


def test_facts_are_searched_even_when_the_session_has_nothing(monkeypatch):
    """The bug found 2026-09-29: a Claude session's id holds no captured messages, so every recall was empty."""
    monkeypatch.setattr(recall_relevant_module, "recall", _by_session({
        "facts:mcp-server": [{"id": "f1", "role": "user", "content": "the vault encrypts credentials with fernet"}],
        "facts:fact-index": [{"id": "i1", "role": "user", "content": "security"}],
        "facts:security": [{"id": "f2", "role": "user", "content": "rotate the memory bearer after a leak"}],
    }))
    out = recall_relevant("claude-session-with-no-history", "rotate bearer leak")
    assert out["total_considered"] == 2
    assert out["messages"][0]["id"] == "f2" and out["messages"][0]["source"] == "fact:security"


def test_one_unreadable_fact_category_never_costs_the_recall_facts(monkeypatch):
    table = _by_session({"facts": [{"id": "g1", "role": "user", "content": "general fact about memory"}]})

    def flaky(session_id, **kw):
        if session_id == "facts:mcp-server":
            raise recall_relevant_module.MemoryError("backend refused")
        return table(session_id, **kw)
    monkeypatch.setattr(recall_relevant_module, "recall", flaky)
    assert recall_relevant("s", "memory")["messages"][0]["id"] == "g1"


def test_a_superseding_fact_hides_the_old_one(monkeypatch):
    """2026-09-29: a SUPERSEDES fact must hide the fact it replaces (ids may be given in short form)."""
    messages = [
        {"id": "msg-5149afd9aaaa", "role": "user", "content": "the starter kit lives at the old path", "timestamp": "t1"},
        {"id": "msg-4bbab299bbbb", "role": "user",
         "content": "SUPERSEDES msg-5149afd9 (old path): the starter kit lives at the new path", "timestamp": "t2"},
    ]
    monkeypatch.setattr(recall_relevant_module, "recall", _fake_recall(messages))
    ids = {m["id"] for m in recall_relevant("s1", "where does the starter kit live", max_results=10)["messages"]}
    assert "msg-4bbab299bbbb" in ids and "msg-5149afd9aaaa" not in ids


def test_every_memory_category_is_searched():
    assert set(recall_relevant_module.KNOWN_FACT_CATEGORIES) >= {
        "general", "platform", "mcp-server", "memory-stack", "local-ai", "known-gaps", "conventions",
        "external-audits", "bugs-fixed", "tasks", "scripts"}


# --- BM25 + recency (R&D's R2/R10; case list drafted by the local model, qwen3-coder-30b) -------------------------
from tools.memory import recall_relevant as rr  # noqa: E402


def test_a_rare_matching_word_outranks_a_common_one():
    docs = ["docker restart", "docker logs", "docker volume", "starter kit lives in the scripts folder"]
    scores = rr.bm25("docker starter", docs)
    assert scores[3] > scores[0] > 0  # "starter" appears once, "docker" three times


def test_a_document_with_no_query_words_scores_exactly_zero():
    assert rr.bm25("vault key", ["the studio makes videos", "vault key rotation"])[0] == 0


def test_a_long_document_does_not_win_by_length():
    short = "memory core runs on docker host"
    long = "memory core " + " ".join(f"filler{i}" for i in range(60))
    scores = rr.bm25("memory core docker host", [short, long])
    assert scores[0] > scores[1]


def test_repeating_a_word_has_diminishing_returns():
    once, twice, five = rr.bm25("shedkeeper", ["shedkeeper decides", "shedkeeper shedkeeper decides", "shedkeeper shedkeeper shedkeeper shedkeeper shedkeeper decides"])
    assert once < twice < five and (twice - once) > (five - twice) / 3


def test_recency_splits_otherwise_equal_scores():
    msgs = [{"id": "old", "content": "starter kit location", "timestamp": "2026-09-20T10:00:00Z"},
            {"id": "new", "content": "starter kit location", "timestamp": "2026-09-29T10:00:00Z"}]
    factors = rr._recency(msgs)
    assert factors == [0.9, 1.1]


def test_a_single_message_gets_the_oldest_factor_which_changes_no_order():
    assert rr._recency([{"timestamp": "2026-09-29T10:00:00Z"}]) == [0.9]


# --- the meaning arm (R10's next step) --------------------------------------------------------------------------------
def test_fusion_lets_a_paraphrase_outrank_a_weak_word_match():
    lexical = [0.0, 0.4, 0.0]          # only the middle one shares a word
    semantic = [0.91, 0.20, 0.35]      # but the first one means the same thing
    fused = rr._fuse(lexical, semantic)
    assert fused[0] > fused[2] and fused[0] > 0 and fused[1] > fused[2]


def test_results_carry_both_scores_when_meaning_is_available(monkeypatch):
    messages = [{"id": "1", "role": "user", "content": "memory-core runs on the server", "timestamp": "t1"},
                {"id": "2", "role": "user", "content": "the studio makes promo videos", "timestamp": "t2"}]
    monkeypatch.setattr(recall_relevant_module, "recall", _fake_recall(messages))
    monkeypatch.setattr(rr, "_semantic", lambda q, docs: [0.9, 0.1])
    out = recall_relevant("s1", "which machine hosts the memory database", max_results=2)
    assert [m["id"] for m in out["messages"]][0] == "1"
    assert out["messages"][0]["semantic"] == 0.9 and "_order" not in out["messages"][0]


def test_a_failed_meaning_call_rests_the_arm_instead_of_waiting_every_time(monkeypatch):
    import httpx
    calls = []
    monkeypatch.setenv("LOCAL_AI_DECIDE_BASE_URL", "http://gpu.test:8082")
    monkeypatch.setattr(rr, "_semantic_rest_until", 0.0)
    monkeypatch.setattr("vault.secret", lambda name, default=None: "t0ken" if name == "GPU_SERVICE_TOKEN" else default)

    def down(*a, **k):
        calls.append(1)
        raise httpx.ConnectError("PC is off")
    monkeypatch.setattr(httpx, "post", down)
    assert rr._semantic("q", ["a"]) is None and rr._semantic("q", ["a"]) is None
    assert len(calls) == 1  # the second one didn't try: it waits out SEMANTIC_REST_S
