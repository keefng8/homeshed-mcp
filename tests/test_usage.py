"""usage.py: sizes-only token-savings accounting. The key property: estimates follow the
per-capability rules, and record() never raises into a real capability call."""
from __future__ import annotations

import json

import pytest

import usage


@pytest.fixture(autouse=True)
def usage_file(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    monkeypatch.setattr(usage, "USAGE_FILE", path)
    return path


def test_generation_counts_local_output_only():
    usage.record("local_ai.ask", {"prompt": "x" * 4000}, {"text": "y" * 400, "model": "q"}, ok=True)
    s = usage.summary()
    assert s["estimated_tokens_saved"] == 100  # 400 chars of output / 4; the prompt doesn't count
    assert s["by_rule"] == {"generation": 100}


def test_summarize_file_counts_file_minus_summary():
    result = {"summary": "s" * 100, "file_chars": 40_000}
    usage.record("local_ai.summarize_file", {"path": "p"}, result, ok=True)
    out_len = len(json.dumps(result))
    assert usage.summary()["estimated_tokens_saved"] == (40_000 - out_len) // 4


def test_knowledge_counts_unique_source_files():
    result = {"results": [{"path": "a", "file_chars": 8000, "text": "t"},
                          {"path": "a", "file_chars": 8000, "text": "t"},
                          {"path": "b", "file_chars": 4000, "text": "t"}]}
    usage.record("knowledge.search", {"query": "q"}, result, ok=True)
    assert usage.summary()["estimated_tokens_saved"] == (12_000 - len(json.dumps(result))) // 4


def test_other_capabilities_save_nothing_but_are_counted():
    usage.record("docker.container.list", {}, [{"name": "x"}], ok=True)
    s = usage.summary()
    assert s["estimated_tokens_saved"] == 0 and s["calls"] == 1


def test_failures_count_errors_not_savings():
    usage.record("local_ai.ask", {"prompt": "x"}, None, ok=False)
    c = usage.summary()["capabilities"]["local_ai.ask"]
    assert c["errors"] == 1 and c["saved_chars"] == 0


def test_images_counted():
    usage.record("image.generate", {"prompt": "p"}, {"job_id": "a"}, ok=True)
    usage.record("image.generate", {"prompt": "p"}, {"job_id": "b"}, ok=True)
    assert usage.summary()["images_generated"] == 2


def test_persists_across_loads(usage_file):
    usage.record("local_ai.ask", {}, {"text": "y" * 40}, ok=True)
    assert json.loads(usage_file.read_text())["capabilities"]["local_ai.ask"]["calls"] == 1
    usage.record("local_ai.ask", {}, {"text": "y" * 40}, ok=True)
    assert usage.summary()["capabilities"]["local_ai.ask"]["calls"] == 2


def test_record_never_raises(monkeypatch):
    monkeypatch.setattr(usage, "USAGE_FILE", usage.Path("/nonexistent-dir/\0bad/usage.json"))
    usage.record("local_ai.ask", {}, {"text": "y"}, ok=True)  # must not raise


def test_unserialisable_values_do_not_break_sizes():
    usage.record("local_ai.ask", {"obj": object()}, {"text": "abcd"}, ok=True)
    assert usage.summary()["estimated_tokens_saved"] == 1


def test_a_get_to_a_listed_write_tool_counts_as_a_read(monkeypatch):
    import usage as u
    monkeypatch.setattr(u, "READ_BY_METHOD", {"svc.call": "svc.read"})
    assert u.ledger_id("svc.call", {"method": "GET", "path": "/x"}) == "svc.read"
    assert u.ledger_id("svc.call", {"method": "post"}) == "svc.call"
    assert u.ledger_id("svc.call", None) == "svc.call"
    assert u.ledger_id("git.commit", {"method": "GET"}) == "git.commit"  # only tools listed

def test_calls_errors_refusals_and_savings_land_in_their_hour(monkeypatch):
    """The Control Panel's Pulse chart: per-hour counts, since the totals keep no history."""
    now = [7200.0 + 5]
    monkeypatch.setattr(usage.time, "time", lambda: now[0])
    usage.record("local_ai.ask", {}, {"text": "y" * 400}, ok=True)          # hour 7200: a call that saved 400 chars
    usage.record("local_ai.ask", {}, None, ok=False)                        # hour 7200: an error
    now[0] = 7200.0 + 3700
    usage.record_refusal("app1")                                          # hour 10800
    usage.record_front_door("app1", hidden_chars=40_000)                    # hour 10800
    usage.record_front_door("app1", trimmed_chars=8_000)
    hours = usage.summary()["hours"]
    assert hours == [{"t": 7200, "calls": 2, "errors": 1, "refused": 0, "saved_chars": 400, "fd_chars": 0},
                     {"t": 10800, "calls": 0, "errors": 0, "refused": 1, "saved_chars": 0, "fd_chars": 48_000}]


def test_hour_buckets_stay_bounded(monkeypatch):
    monkeypatch.setattr(usage, "MAX_HOURS", 3)
    now = [0.0]
    monkeypatch.setattr(usage.time, "time", lambda: now[0])
    for h in range(10):
        now[0] = h * 3600.0
        usage.record("docker.container.list", {}, [], ok=True)
    assert [h["t"] for h in usage.summary()["hours"]] == [7 * 3600, 8 * 3600, 9 * 3600]  # the newest three


def test_front_door_totals_add_every_app_and_name_none():
    usage.record_front_door("app1", hidden_chars=40_000)
    usage.record_front_door("app2", hidden_chars=4_000)
    usage.record_front_door("app2", trimmed_chars=1_000)
    assert usage.summary()["front_door_total"] == {"listings": 2, "hidden_chars": 44_000, "calls": 1,
                                                   "trimmed_chars": 1_000}
    assert "app1" not in json.dumps(usage.summary()["front_door_total"])


def test_an_old_usage_file_without_hours_still_summarises(usage_file):
    usage_file.write_text(json.dumps({"since": 1, "capabilities": {}}))
    s = usage.summary()
    assert s["hours"] == [] and s["front_door_total"] == {"listings": 0, "hidden_chars": 0, "calls": 0, "trimmed_chars": 0}
