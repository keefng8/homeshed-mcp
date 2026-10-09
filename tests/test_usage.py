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
