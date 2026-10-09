"""/local-ai/decisions and /local-ai/backends/{name}/reset custom routes (2026-09-28): the
dashboard's "Why this model?" view and its "try again now" button for a benched model. Case list
drafted by local_ai.ask (Qwen3-Coder-30B); limits are clamped here rather than reset to 20."""
import asyncio
import importlib
import json
import sys
import time

import pytest


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    return importlib.import_module("server")


class Req:
    def __init__(self, query=None, path=None):
        self.query_params = query or {}
        self.path_params = path or {}


def _decisions(server, monkeypatch, n, query):
    from tools.local_ai import ask
    log = ask.collections.deque([{"t": i, "chosen": f"m{i}"} for i in range(n)], maxlen=ask.DECISIONS_MAX)
    monkeypatch.setattr(ask, "_decisions", log)
    return json.loads(asyncio.run(server.local_ai_decisions(Req(query))).body)


def test_decisions_default_limit_and_bench_window(server, monkeypatch):
    body = _decisions(server, monkeypatch, 30, {})
    assert len(body["decisions"]) == 20 and body["bench_s"] == 60
    assert body["decisions"][0]["chosen"] == "m0"  # newest first, as stored


@pytest.mark.parametrize("limit, expected", [("5", 5), ("0", 1), ("100", 50), ("junk", 20)])
def test_decisions_limit_is_clamped(server, monkeypatch, limit, expected):
    assert len(_decisions(server, monkeypatch, 50, {"limit": limit})["decisions"]) == expected


def _reset(server, monkeypatch, name, benched):
    from tools.local_ai import ask
    monkeypatch.setattr(ask, "backends", lambda: [{"name": "qwen3-coder"}, {"name": "healthy"}])
    monkeypatch.setattr(ask, "_benched_until", {"qwen3-coder": time.time() + 30} if benched else {})
    return asyncio.run(server.local_ai_reset_bench(Req(path={"name": name})))


def test_reset_lifts_a_bench(server, monkeypatch):
    from tools.local_ai import ask
    resp = _reset(server, monkeypatch, "qwen3-coder", benched=True)
    assert json.loads(resp.body) == {"name": "qwen3-coder", "was_benched": True}
    assert ask.bench_remaining("qwen3-coder") == 0


def test_reset_of_a_healthy_model_is_a_no_op(server, monkeypatch):
    resp = _reset(server, monkeypatch, "healthy", benched=False)
    assert json.loads(resp.body) == {"name": "healthy", "was_benched": False}


def test_reset_unknown_model_is_404(server, monkeypatch):
    resp = _reset(server, monkeypatch, "nope", benched=False)
    assert resp.status_code == 404 and "unknown model" in json.loads(resp.body)["error"]
