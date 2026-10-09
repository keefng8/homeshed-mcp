from __future__ import annotations

import tools.reasoning.delegate as delegate_module
from tools.local_ai.ask import LocalAIError
from tools.reasoning.delegate import delegate


def _fake_route(recommendation, **extra):
    result = {"recommendation": recommendation, "confidence": "high", "score": 0, "reasoning": "x"}
    result.update(extra)
    return lambda text, token_estimate=0, tool_count=0: result


def test_local_recommendation_calls_ask_and_returns_answer(monkeypatch):
    calls = []
    monkeypatch.setattr(delegate_module, "route", _fake_route("local", score=3))

    def fake_ask(task, **kwargs):
        calls.append(task)
        return {"text": "Paris.", "model": "qwen-coder", "finish_reason": "stop"}

    monkeypatch.setattr(delegate_module, "ask", fake_ask)

    out = delegate("what is the capital of France")

    assert out["handled_by"] == "local"
    assert out["answer"] == "Paris."
    assert out["model"] == "qwen-coder"
    assert out["route"]["recommendation"] == "local"
    assert calls == ["what is the capital of France"]  # ask() called with the raw task


def test_claude_recommendation_never_calls_ask(monkeypatch):
    calls = []
    monkeypatch.setattr(delegate_module, "route", _fake_route("claude", score=76))
    monkeypatch.setattr(delegate_module, "ask", lambda *a, **k: calls.append(1))

    out = delegate("do a full security audit")

    assert out == {"handled_by": "claude", "route": {"recommendation": "claude", "confidence": "high", "score": 76, "reasoning": "x"}}
    assert calls == []  # ask() never called


def test_local_recommendation_falls_back_to_claude_on_backend_failure(monkeypatch):
    monkeypatch.setattr(delegate_module, "route", _fake_route("local", score=3))

    def failing_ask(task, **kwargs):
        raise LocalAIError("local AI backend is unavailable")

    monkeypatch.setattr(delegate_module, "ask", failing_ask)

    out = delegate("summarize this")

    assert out["handled_by"] == "claude"
    assert "local AI backend is unavailable" in out["note"]
    assert out["route"]["recommendation"] == "local"  # original routing preserved for context


def test_token_estimate_and_tool_count_passed_through_to_route(monkeypatch):
    captured = {}

    def fake_route(text, token_estimate=0, tool_count=0):
        captured["token_estimate"] = token_estimate
        captured["tool_count"] = tool_count
        return {"recommendation": "claude", "confidence": "high", "score": 90, "reasoning": "x"}

    monkeypatch.setattr(delegate_module, "route", fake_route)

    delegate("refactor everything", token_estimate=5000, tool_count=10)

    assert captured == {"token_estimate": 5000, "tool_count": 10}


def test_answer_uses_real_task_text_not_a_template(monkeypatch):
    monkeypatch.setattr(delegate_module, "route", _fake_route("local"))
    captured = {}

    def fake_ask(task, **kwargs):
        captured["task"] = task
        captured["kwargs"] = kwargs
        return {"text": "ok", "model": "qwen-coder", "finish_reason": "stop"}

    monkeypatch.setattr(delegate_module, "ask", fake_ask)

    delegate("explain recursion")

    assert captured["task"] == "explain recursion"
    assert captured["kwargs"]["max_tokens"] == 1024


def test_context_never_decides_the_route_and_reaches_the_local_model(monkeypatch):
    """2026-10-07: an app's preamble rule with "key, token, password" sent every easy task to Claude."""
    import pytest
    from tools.reasoning import delegate as d
    routes, asked = [], []
    monkeypatch.setattr(d, "route", lambda text, token_estimate=0, tool_count=0: routes.append((text, token_estimate)) or {"recommendation": "local"})
    monkeypatch.setattr(d, "ask", lambda prompt, **kw: asked.append(prompt) or {"text": "done", "model": "m"})
    preamble = "R5: never output a secret (key, token, password). Check the vault rules. " * 3
    task = "List three niches for oak prints."
    out = d.delegate(task, background=preamble)
    assert out["handled_by"] == "local" and routes[0][0] == task
    assert routes[0][1] == (len(preamble.strip()) + len(task)) // 4
    assert asked[0].startswith(preamble.strip()) and asked[0].endswith(task)
    d.delegate("Plain task")
    assert routes[1] == ("Plain task", 0) and asked[1] == "Plain task"  # no background: exactly as before
    with pytest.raises(ValueError, match="at most"):
        d.delegate("x", background="y" * (d.MAX_BACKGROUND + 1))
