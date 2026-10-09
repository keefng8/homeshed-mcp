from __future__ import annotations

import pytest

import tools.reasoning.decompose_task as decompose_task_module
from tools.local_ai.ask import LocalAIError
from tools.reasoning.decompose_task import decompose_task


def test_clean_json_array_response_parses_first_try(monkeypatch):
    calls = []

    def fake_ask(prompt, **kwargs):
        calls.append(prompt)
        return {"text": '["step one", "step two", "step three"]', "model": "qwen-coder", "finish_reason": "stop"}

    monkeypatch.setattr(decompose_task_module, "ask", fake_ask)

    out = decompose_task("do the thing")

    assert out == {"task": "do the thing", "subtasks": ["step one", "step two", "step three"], "model": "qwen-coder"}
    assert len(calls) == 1  # no retry needed


def test_response_wrapped_in_prose_or_fences_still_parses(monkeypatch):
    def fake_ask(prompt, **kwargs):
        return {"text": 'Sure, here you go:\n```json\n["a", "b"]\n```\nHope that helps!', "model": "qwen-coder", "finish_reason": "stop"}

    monkeypatch.setattr(decompose_task_module, "ask", fake_ask)

    out = decompose_task("do the thing")

    assert out["subtasks"] == ["a", "b"]


def test_malformed_first_response_triggers_one_retry_that_succeeds(monkeypatch):
    calls = []

    def fake_ask(prompt, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return {"text": "I cannot help with that.", "model": "qwen-coder", "finish_reason": "stop"}
        return {"text": '["retry step"]', "model": "qwen-coder", "finish_reason": "stop"}

    monkeypatch.setattr(decompose_task_module, "ask", fake_ask)

    out = decompose_task("do the thing")

    assert out["subtasks"] == ["retry step"]
    assert len(calls) == 2
    assert "must start with" in calls[1]  # the stricter retry prompt


def test_malformed_response_twice_raises_runtime_error(monkeypatch):
    def fake_ask(prompt, **kwargs):
        return {"text": "no json here at all", "model": "qwen-coder", "finish_reason": "stop"}

    monkeypatch.setattr(decompose_task_module, "ask", fake_ask)

    with pytest.raises(RuntimeError, match="did not return a parseable JSON array"):
        decompose_task("do the thing")


def test_non_string_array_items_treated_as_unparseable(monkeypatch):
    calls = []

    def fake_ask(prompt, **kwargs):
        calls.append(prompt)
        return {"text": "[1, 2, 3]", "model": "qwen-coder", "finish_reason": "stop"}

    monkeypatch.setattr(decompose_task_module, "ask", fake_ask)

    with pytest.raises(RuntimeError, match="did not return a parseable JSON array"):
        decompose_task("do the thing")
    assert len(calls) == 2  # retried once, still failed


def test_local_ai_error_reraised_as_runtime_error(monkeypatch):
    def fake_ask(prompt, **kwargs):
        raise LocalAIError("local AI backend is unavailable")

    monkeypatch.setattr(decompose_task_module, "ask", fake_ask)

    with pytest.raises(RuntimeError, match="local AI backend unavailable for decomposition"):
        decompose_task("do the thing")


def test_empty_task_raises_value_error():
    with pytest.raises(ValueError, match="non-empty"):
        decompose_task("")


def test_whitespace_only_task_raises_value_error():
    with pytest.raises(ValueError, match="non-empty"):
        decompose_task("   ")


def test_max_subtasks_clamped_and_truncates_result(monkeypatch):
    def fake_ask(prompt, **kwargs):
        return {"text": '["a", "b", "c", "d", "e"]', "model": "qwen-coder", "finish_reason": "stop"}

    monkeypatch.setattr(decompose_task_module, "ask", fake_ask)

    out = decompose_task("do the thing", max_subtasks=2)

    assert out["subtasks"] == ["a", "b"]


def test_max_subtasks_clamp_reflected_in_prompt(monkeypatch):
    captured = {}

    def fake_ask(prompt, **kwargs):
        captured["prompt"] = prompt
        return {"text": '["a"]', "model": "qwen-coder", "finish_reason": "stop"}

    monkeypatch.setattr(decompose_task_module, "ask", fake_ask)

    decompose_task("do the thing", max_subtasks=999)

    assert "at most 20 items" in captured["prompt"]  # clamped from 999 down to the max, not rejected
