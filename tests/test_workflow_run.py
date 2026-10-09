from __future__ import annotations

import pytest

from registry import Capability
import tools.workflow.run as workflow_run


def _fake_capability(capability_id: str, func):
    category, name = capability_id.split(".", 1)
    return Capability(name=name, category=category, doc="fake.md", func=func)


def _patch_registry(monkeypatch, capabilities: dict[str, Capability]):
    monkeypatch.setattr(workflow_run, "get_capability", lambda cid: capabilities.get(cid))


def test_runs_all_steps_in_order_and_reports_ok(monkeypatch):
    calls = []

    def step_a(x):
        calls.append(("a", x))
        return {"a_result": x}

    def step_b():
        calls.append(("b",))
        return {"b_result": True}

    _patch_registry(monkeypatch, {
        "fake.a": _fake_capability("fake.a", step_a),
        "fake.b": _fake_capability("fake.b", step_b),
    })

    out = workflow_run.run(steps=[
        {"capability": "fake.a", "arguments": {"x": 1}},
        {"capability": "fake.b"},
    ])

    assert calls == [("a", 1), ("b",)]
    assert out["ok"] is True
    assert out["completed"] == 2
    assert out["total"] == 2
    assert out["steps"][0] == {"capability": "fake.a", "ok": True, "result": {"a_result": 1}}
    assert out["steps"][1] == {"capability": "fake.b", "ok": True, "result": {"b_result": True}}


def test_stops_at_first_failure_and_never_runs_later_steps(monkeypatch):
    calls = []

    def step_a():
        calls.append("a")
        raise RuntimeError("boom")

    def step_b():
        calls.append("b")
        return {}

    _patch_registry(monkeypatch, {
        "fake.a": _fake_capability("fake.a", step_a),
        "fake.b": _fake_capability("fake.b", step_b),
    })

    out = workflow_run.run(steps=[{"capability": "fake.a"}, {"capability": "fake.b"}])

    assert calls == ["a"]  # step_b never ran
    assert out["ok"] is False
    assert out["completed"] == 1
    assert out["total"] == 2
    assert out["steps"] == [{"capability": "fake.a", "ok": False, "error": "boom"}]


def test_unknown_capability_raises_before_any_step_runs(monkeypatch):
    calls = []

    def step_a():
        calls.append("a")
        return {}

    _patch_registry(monkeypatch, {"fake.a": _fake_capability("fake.a", step_a)})

    with pytest.raises(ValueError, match="unknown capability 'fake.missing'"):
        workflow_run.run(steps=[{"capability": "fake.a"}, {"capability": "fake.missing"}])

    assert calls == []  # fake.a never ran even though it comes first -- validated up front


def test_empty_steps_raises(monkeypatch):
    with pytest.raises(ValueError, match="non-empty"):
        workflow_run.run(steps=[])


def test_malformed_step_raises(monkeypatch):
    with pytest.raises(ValueError, match="must be a dict with a 'capability' key"):
        workflow_run.run(steps=["not-a-dict"])


def test_step_without_capability_key_raises(monkeypatch):
    with pytest.raises(ValueError, match="must be a dict with a 'capability' key"):
        workflow_run.run(steps=[{"arguments": {}}])


def test_missing_arguments_defaults_to_empty_dict(monkeypatch):
    def step_a(**kwargs):
        return kwargs

    _patch_registry(monkeypatch, {"fake.a": _fake_capability("fake.a", step_a)})

    out = workflow_run.run(steps=[{"capability": "fake.a"}])

    assert out["steps"][0]["result"] == {}


# --- $id references: one step's output feeding a later step's input (2026-09-24) -----------
# Closes workflow.run's own documented limitation (couldn't pass step output as later input),
# the pattern adapted from reference-repos/bernstein's depends_on -- value passing only, no DAG
# scheduler or parallel branches (workflow.run stays deliberately sequential and small).


def test_nested_value_from_earlier_step_feeds_later_step(monkeypatch):
    received = {}

    def clone(url):
        return {"destination": "/tmp/repo", "branch": "main"}

    def status(path):
        received["path"] = path
        return {"clean": True}

    _patch_registry(monkeypatch, {
        "fake.clone": _fake_capability("fake.clone", clone),
        "fake.status": _fake_capability("fake.status", status),
    })

    out = workflow_run.run(steps=[
        {"id": "c", "capability": "fake.clone", "arguments": {"url": "x"}},
        {"capability": "fake.status", "arguments": {"path": "$c.destination"}},
    ])

    assert out["ok"] is True
    assert received["path"] == "/tmp/repo"


def test_whole_result_dict_reference(monkeypatch):
    received = {}

    def producer():
        return {"a": 1, "b": 2}

    def consumer(data):
        received["data"] = data
        return {}

    _patch_registry(monkeypatch, {
        "fake.p": _fake_capability("fake.p", producer),
        "fake.c": _fake_capability("fake.c", consumer),
    })

    workflow_run.run(steps=[
        {"id": "p", "capability": "fake.p"},
        {"capability": "fake.c", "arguments": {"data": "$p"}},
    ])

    assert received["data"] == {"a": 1, "b": 2}


def test_reference_nested_inside_list_and_dict_args(monkeypatch):
    received = {}

    def producer():
        return {"id": "abc"}

    def consumer(items, meta):
        received["items"] = items
        received["meta"] = meta
        return {}

    _patch_registry(monkeypatch, {
        "fake.p": _fake_capability("fake.p", producer),
        "fake.c": _fake_capability("fake.c", consumer),
    })

    workflow_run.run(steps=[
        {"id": "p", "capability": "fake.p"},
        {"capability": "fake.c", "arguments": {"items": ["$p.id", "literal"], "meta": {"ref": "$p.id"}}},
    ])

    assert received["items"] == ["abc", "literal"]
    assert received["meta"] == {"ref": "abc"}


def test_forward_reference_raises_before_anything_runs(monkeypatch):
    calls = []

    def step(**kwargs):
        calls.append(kwargs)
        return {}

    _patch_registry(monkeypatch, {"fake.a": _fake_capability("fake.a", step)})

    with pytest.raises(ValueError, match="comes later in the list"):
        workflow_run.run(steps=[
            {"capability": "fake.a", "arguments": {"x": "$later.value"}},
            {"id": "later", "capability": "fake.a"},
        ])

    assert calls == []


def test_unknown_reference_raises_before_anything_runs(monkeypatch):
    calls = []

    def step(**kwargs):
        calls.append(kwargs)
        return {}

    _patch_registry(monkeypatch, {"fake.a": _fake_capability("fake.a", step)})

    with pytest.raises(ValueError, match="doesn't exist"):
        workflow_run.run(steps=[
            {"id": "first", "capability": "fake.a"},
            {"capability": "fake.a", "arguments": {"x": "$typo"}},
        ])

    assert calls == []


def test_missing_path_fails_that_step_and_stops(monkeypatch):
    calls = []

    def producer():
        return {"a": 1}

    def consumer(x):
        calls.append(x)
        return {}

    _patch_registry(monkeypatch, {
        "fake.p": _fake_capability("fake.p", producer),
        "fake.c": _fake_capability("fake.c", consumer),
    })

    out = workflow_run.run(steps=[
        {"id": "p", "capability": "fake.p"},
        {"capability": "fake.c", "arguments": {"x": "$p.missing"}},
    ])

    assert out["ok"] is False
    assert out["completed"] == 2
    assert out["steps"][1]["ok"] is False
    assert "$p.missing" in out["steps"][1]["error"]
    assert calls == []


def test_dollar_literal_that_is_not_a_reference_passes_through(monkeypatch):
    received = {}

    def consumer(price):
        received["price"] = price
        return {}

    _patch_registry(monkeypatch, {"fake.c": _fake_capability("fake.c", consumer)})

    out = workflow_run.run(steps=[
        {"capability": "fake.c", "arguments": {"price": "$5.00"}},
    ])

    assert out["ok"] is True
    assert received["price"] == "$5.00"
