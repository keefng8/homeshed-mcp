from __future__ import annotations

import tools.reasoning.pipeline as pipeline_module
from tools.memory.capture import MemoryError
from tools.reasoning.pipeline import pipeline


def _fake_delegate_local(answer="Paris.", model="qwen-coder"):
    def fake(task, token_estimate=0, tool_count=0):
        return {
            "handled_by": "local",
            "answer": answer,
            "model": model,
            "route": {"recommendation": "local", "confidence": "high", "score": 3, "reasoning": "x"},
        }
    return fake


def _fake_delegate_claude():
    def fake(task, token_estimate=0, tool_count=0):
        return {
            "handled_by": "claude",
            "route": {"recommendation": "claude", "confidence": "high", "score": 76, "reasoning": "security audit"},
        }
    return fake


def test_local_outcome_persisted_to_memory(monkeypatch):
    monkeypatch.setattr(pipeline_module, "delegate", _fake_delegate_local())
    captured = {}

    def fake_capture(session_id, content, role="user"):
        captured["session_id"] = session_id
        captured["content"] = content
        return {"accepted": True, "message_id": "msg-123"}

    monkeypatch.setattr(pipeline_module, "capture", fake_capture)

    out = pipeline("capital of France")

    assert out["handled_by"] == "local"
    assert out["answer"] == "Paris."
    assert out["remembered"] is True
    assert out["message_id"] == "msg-123"
    assert captured["session_id"] == "reasoning-pipeline-log"
    assert "Paris." in captured["content"]
    assert "capital of France" in captured["content"]


def test_claude_outcome_persisted_without_a_fake_answer(monkeypatch):
    monkeypatch.setattr(pipeline_module, "delegate", _fake_delegate_claude())
    captured = {}

    def fake_capture(session_id, content, role="user"):
        captured["content"] = content
        return {"accepted": True, "message_id": "msg-456"}

    monkeypatch.setattr(pipeline_module, "capture", fake_capture)

    out = pipeline("do a full security audit")

    assert out["handled_by"] == "claude"
    assert "answer" not in out
    assert out["remembered"] is True
    assert "security audit" in captured["content"]  # the route reasoning, not a fake answer


def test_remember_false_skips_memory_entirely(monkeypatch):
    monkeypatch.setattr(pipeline_module, "delegate", _fake_delegate_local())
    calls = []
    monkeypatch.setattr(pipeline_module, "capture", lambda *a, **k: calls.append(1))

    out = pipeline("capital of France", remember=False)

    assert out["remembered"] is False
    assert out["message_id"] is None
    assert calls == []


def test_memory_failure_does_not_hide_the_real_delegate_result(monkeypatch):
    monkeypatch.setattr(pipeline_module, "delegate", _fake_delegate_local(answer="42."))

    def failing_capture(session_id, content, role="user"):
        raise MemoryError("memory-core unreachable")

    monkeypatch.setattr(pipeline_module, "capture", failing_capture)

    out = pipeline("what is 6 times 7")

    assert out["handled_by"] == "local"
    assert out["answer"] == "42."  # the real result, not swallowed by the memory failure
    assert out["remembered"] is False
    assert out["message_id"] is None
    assert "memory-core unreachable" in out["memory_error"]


def test_token_estimate_and_tool_count_passed_through_to_delegate(monkeypatch):
    captured = {}

    def fake_delegate(task, token_estimate=0, tool_count=0):
        captured["token_estimate"] = token_estimate
        captured["tool_count"] = tool_count
        return {"handled_by": "claude", "route": {"recommendation": "claude", "confidence": "high", "score": 90, "reasoning": "x"}}

    monkeypatch.setattr(pipeline_module, "delegate", fake_delegate)
    monkeypatch.setattr(pipeline_module, "capture", lambda *a, **k: {"accepted": True, "message_id": "m"})

    pipeline("refactor everything", token_estimate=5000, tool_count=10)

    assert captured == {"token_estimate": 5000, "tool_count": 10}
