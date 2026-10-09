"""Tests for local_ai.ask: the registry-driven delegator (2026-09-26). Case list drafted by
local_ai.ask (qwen-coder), written by hand. No live backends: httpx is faked; the registry is a
temp file."""
import json
from unittest.mock import MagicMock

import httpx
import pytest

from tools.local_ai import ask as module

CODER, AGENT = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", "qwen3-coder-30b"
SUPER, ULTRA = "nvidia-nemotron-3-super-120b-a12b", "nvidia-nemotron-3-ultra-550b-a55b"
REGISTRY = {"backends": [
    {"name": "qwen-coder", "url_env": "LOCAL_AI_CODER_BASE_URL", "model_env": "LOCAL_AI_CODER_MODEL", "cloud": False, "tier": 1},
    {"name": "qwen3-coder", "aliases": ["qwen3"], "url_env": "LOCAL_AI_BASE_URL", "model": AGENT, "cloud": False, "tier": 1},
    {"name": "gateway", "discover": True, "url_env": "LOCAL_AI_GATEWAY_URL", "key_env": "LOCAL_AI_GATEWAY_KEY",
     "include": ["nvidia-*"], "cloud": True, "tier": 3,
     "aliases": {"nemotron-super": SUPER, "nemotron-ultra": ULTRA}},
]}


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    reg = tmp_path / "backends.json"
    reg.write_text(json.dumps(REGISTRY))
    monkeypatch.setattr(module, "REGISTRY_FILE", reg)
    monkeypatch.setenv("LOCAL_AI_CODER_BASE_URL", "http://localhost:8081/v1")
    monkeypatch.setenv("LOCAL_AI_CODER_MODEL", CODER)
    monkeypatch.setenv("LOCAL_AI_BASE_URL", "http://localhost:8080/v1")
    monkeypatch.setenv("LOCAL_AI_GATEWAY_URL", "http://gateway:4000/v1")
    monkeypatch.setenv("LOCAL_AI_GATEWAY_KEY", "sk-test")
    monkeypatch.setattr(module, "_discovery_cache", {})
    monkeypatch.setattr(module, "_benched_until", {})
    monkeypatch.setattr(module, "_rotation", {})
    monkeypatch.setattr(module, "_is_busy", lambda base_url: False)
    monkeypatch.setattr(module.httpx, "get", _gateway_models([SUPER, ULTRA, "qwen-local", "qwen-coder"]))
    return reg


def _gateway_models(ids, status=200):
    def fake_get(url, **kw):
        r = MagicMock(status_code=status)
        r.json.return_value = {"data": [{"id": i} for i in ids]}
        return r
    return fake_get


def _ok(text="hello"):
    r = MagicMock(status_code=200)
    r.json.return_value = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    return r


def _routed(responses, monkeypatch):
    calls = []

    def fake(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        outcome = responses.get(json["model"], httpx.ConnectError("not expected"))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(module.httpx, "post", fake)
    return calls


def test_discovered_by_registry():
    from registry import discover

    assert len([c for c in discover() if c.category == "local_ai" and c.name == "ask"]) == 1


def test_discovery_adds_gateway_models_filtered_by_include():
    names = {b["name"] for b in module.backends()}
    assert {SUPER, ULTRA} <= names
    assert "qwen-local" not in names  # gateway aliases for local models never double-route


def test_new_gateway_model_is_used_without_code_changes(monkeypatch):
    monkeypatch.setattr(module.httpx, "get", _gateway_models([SUPER, ULTRA, "nvidia-brand-new-model"]))
    assert "nvidia-brand-new-model" in {b["name"] for b in module.backends()}


def test_new_registry_entry_is_used_without_code_changes(env, monkeypatch):
    reg = json.loads(env.read_text())
    reg["backends"].append({"name": "new-local", "url": "http://localhost:9999/v1", "model": "m", "cloud": False, "tier": 1})
    env.write_text(json.dumps(reg))
    assert "new-local" in {b["name"] for b in module.backends()}


def test_round_robin_spreads_load_across_a_tier(monkeypatch):
    calls = _routed({CODER: _ok(), AGENT: _ok()}, monkeypatch)
    used = [module.ask("hi")["backend"] for _ in range(4)]
    assert set(used) == {"qwen-coder", "qwen3-coder"}  # both tier-1 models get work
    assert all("localhost" in c["url"] for c in calls)  # cloud not touched while local works


def test_local_tier_before_cloud(monkeypatch):
    calls = _routed({CODER: _ok()}, monkeypatch)
    monkeypatch.setattr(module, "_is_busy", lambda base_url: "8080" in base_url)
    assert module.ask("hi")["backend"] == "qwen-coder"


def test_busy_locals_overflow_to_cloud_with_key_and_thinking_off(monkeypatch):
    monkeypatch.setattr(module, "_is_busy", lambda base_url: True)
    calls = _routed({SUPER: _ok("nv"), ULTRA: _ok("nv")}, monkeypatch)
    out = module.ask("hi")
    assert out["backend"] in (SUPER, ULTRA)
    assert {s["reason"] for s in out["skipped"]} == {"busy"}
    assert calls[0]["headers"] == {"Authorization": "Bearer sk-test"}
    assert calls[0]["json"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_failed_backend_is_benched_then_retried_after_bench_window(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: t[0])
    _routed({CODER: httpx.ConnectError("down"), AGENT: _ok()}, monkeypatch)
    module.ask("hi"); module.ask("hi")  # at some point qwen-coder fails and gets benched
    out = module.ask("hi")
    assert out["backend"] == "qwen3-coder"
    assert any(s["reason"] == "benched after a recent failure" for s in out["skipped"]) or \
        any(s["backend"] == "qwen-coder" for s in out["skipped"])
    t[0] += module.BENCH_S + 1
    _routed({CODER: _ok("back"), AGENT: _ok()}, monkeypatch)
    assert "qwen-coder" in {module.ask("hi")["backend"] for _ in range(3)}  # tried again


def test_allow_cloud_false_never_calls_cloud(monkeypatch):
    monkeypatch.setattr(module, "_is_busy", lambda base_url: True)
    calls = _routed({}, monkeypatch)
    with pytest.raises(module.LocalAIError, match="cloud not allowed"):
        module.ask("secret", allow_cloud=False)
    assert calls == []


def test_all_fail_lists_reasons_without_urls_or_keys(monkeypatch):
    _routed({m: httpx.ConnectError("refused http://localhost:8081") for m in (CODER, AGENT, SUPER, ULTRA)}, monkeypatch)
    with pytest.raises(module.LocalAIError) as exc:
        module.ask("hi")
    msg = str(exc.value)
    assert "no backend could answer" in msg and "qwen-coder: unavailable" in msg
    assert "localhost" not in msg and "gateway:4000" not in msg and "sk-test" not in msg


def test_discovery_failure_uses_cache_then_nothing_never_raises(monkeypatch):
    assert {SUPER, ULTRA} <= {b["name"] for b in module.backends()}  # fills cache
    monkeypatch.setattr(module.httpx, "get", _gateway_models([], status=500))
    monkeypatch.setattr(module.time, "time", lambda: 10**10)  # cache expired
    assert {SUPER, ULTRA} <= {b["name"] for b in module.backends()}  # last good list kept
    module._discovery_cache.clear()
    assert not any(b["cloud"] for b in module.backends())  # no cache: zero cloud, no error


def test_explicit_model_and_alias_no_fallback(monkeypatch):
    calls = _routed({SUPER: _ok("s"), AGENT: _ok("a"), CODER: httpx.ConnectError("down")}, monkeypatch)
    assert module.ask("hi", model="nemotron-super")["backend"] == SUPER
    assert module.ask("hi", model="qwen3")["backend"] == "qwen3-coder"  # old name still works
    with pytest.raises(module.LocalAIError, match="'qwen-coder' unavailable"):
        module.ask("hi", model="qwen-coder")
    assert len([c for c in calls if CODER == c["json"]["model"]]) == 1


def test_explicit_cloud_with_allow_cloud_false_rejected():
    with pytest.raises(module.LocalAIError, match="allow_cloud"):
        module.ask("hi", model="nemotron-super", allow_cloud=False)


def test_unknown_model_rejected():
    with pytest.raises(module.LocalAIError, match="unknown model"):
        module.ask("hi", model="gpt-4")


def test_unconfigured_explicit_model(monkeypatch):
    monkeypatch.delenv("LOCAL_AI_CODER_BASE_URL")
    with pytest.raises(module.LocalAIError, match="not configured"):
        module.ask("hi", model="qwen-coder")


@pytest.mark.parametrize("kwargs", [{"prompt": ""}, {"prompt": "x" * (module.MAX_PROMPT_CHARS + 1)},
                                    {"prompt": "hi", "temperature": 5.0}, {"prompt": "hi", "max_tokens": 0}])
def test_argument_validation(kwargs):
    with pytest.raises(module.LocalAIError):
        module.ask(**kwargs)


def test_malformed_and_null_responses_fall_through(monkeypatch):
    bad = MagicMock(status_code=200)
    bad.json.return_value = {"unexpected": "shape"}
    null = MagicMock(status_code=200)
    null.json.return_value = {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
    _routed({CODER: bad, AGENT: null, SUPER: _ok(), ULTRA: _ok()}, monkeypatch)
    out = module.ask("hi")
    assert out["backend"] in (SUPER, ULTRA)
    assert {s["reason"] for s in out["skipped"]} >= {"returned an unexpected response shape", "returned no text"}


def test_is_busy_parses_llamacpp_metric(monkeypatch):
    monkeypatch.undo()
    busy = MagicMock(status_code=200, text="llamacpp:requests_processing 1\n")
    idle = MagicMock(status_code=200, text="llamacpp:requests_processing 0\n")
    monkeypatch.setattr(module.httpx, "get", lambda url, **kw: busy)
    assert module._is_busy("http://h:8081/v1") is True
    monkeypatch.setattr(module.httpx, "get", lambda url, **kw: idle)
    assert module._is_busy("http://h:8081/v1") is False


def test_is_busy_only_when_every_slot_is_taken(monkeypatch):
    """Four slots: one peer's request leaves room for more; a full server or a queue is busy (2026-09-29)."""
    monkeypatch.undo()
    module._slots.clear()
    state = {"metrics": ""}

    def fake_get(url, **kw):
        if url.endswith("/props"):
            return MagicMock(status_code=200, json=lambda: {"total_slots": 4})
        return MagicMock(status_code=200, text=state["metrics"])

    monkeypatch.setattr(module.httpx, "get", fake_get)
    for text, busy in (("llamacpp:requests_processing 1\nllamacpp:requests_deferred 0\n", False),
                       ("llamacpp:requests_processing 3\nllamacpp:requests_deferred 0\n", False),
                       ("llamacpp:requests_processing 4\nllamacpp:requests_deferred 0\n", True),
                       ("llamacpp:requests_processing 2\nllamacpp:requests_deferred 1\n", True)):
        state["metrics"] = text
        assert module._is_busy("http://h:8080/v1") is busy, text
    module._slots.clear()


# --- decision log ("Why this model?"), bench countdown and reset (2026-09-28) ---

def test_decision_log_records_choice_and_skips_without_prompt(monkeypatch):
    monkeypatch.setattr(module, "_decisions", module.collections.deque(maxlen=module.DECISIONS_MAX))
    monkeypatch.setattr(module, "_is_busy", lambda base_url: "8080" in base_url)
    _routed({CODER: _ok("secret answer")}, monkeypatch)
    module.ask("a private prompt")
    d = module.decisions()[0]
    assert d["ok"] and d["chosen"] == "qwen-coder" and d["mode"] == "auto" and d["cloud"] is False
    assert {"backend": "qwen3-coder", "reason": "busy"} in d["skipped"] or d["order"][0] == "qwen-coder"
    assert "private" not in str(d) and "secret" not in str(d)


def test_decision_log_records_total_failure_and_explicit_calls(monkeypatch):
    monkeypatch.setattr(module, "_decisions", module.collections.deque(maxlen=module.DECISIONS_MAX))
    _routed({}, monkeypatch)  # every backend fails
    with pytest.raises(module.LocalAIError):
        module.ask("hi")
    fail = module.decisions()[0]
    assert fail["ok"] is False and fail["chosen"] is None and fail["error"] == "no backend could answer"
    assert len(fail["skipped"]) == len(fail["order"])
    _routed({AGENT: _ok()}, monkeypatch)
    module.ask("hi", model="qwen3")
    ex = module.decisions()[0]
    assert ex["mode"] == "explicit" and ex["chosen"] == "qwen3-coder" and ex["skipped"] == []
    assert len(module.decisions(1)) == 1


def test_bench_countdown_and_reset(monkeypatch):
    t = [5000.0]
    monkeypatch.setattr(module.time, "time", lambda: t[0])
    module._benched_until["qwen-coder"] = t[0] + 42
    assert module.bench_remaining("qwen-coder") == 42 and module.bench_remaining("qwen3-coder") == 0
    assert module.reset_bench("qwen-coder") is True and module.bench_remaining("qwen-coder") == 0
    assert module.reset_bench("qwen-coder") is False


# --- the served-model check (R&D's scorecard, 2026-09-30: Qwen3-8B answered under the 30B's name) ---

def test_a_different_model_file_on_the_local_server_is_named(monkeypatch):
    from tools.local_ai import ask as module
    module._props_cache.clear()

    class Props:
        status_code = 200

        def __init__(self, path):
            self.path = path

        def json(self):
            return {"model_path": self.path, "model_alias": "qwen3-coder-30b"}
    b = {"name": "local", "model": "qwen3-coder-30b", "base_url": "http://gpu.test:8080/v1", "cloud": False}
    monkeypatch.setattr(module.httpx, "get", lambda url, timeout: Props("/models/Qwen3-8B-Q6_K.gguf"))
    assert module._served_model(b["base_url"]) == "Qwen3-8B-Q6_K.gguf"
    assert not module._same_model(b["model"], "Qwen3-8B-Q6_K.gguf")
    assert module._same_model(b["model"], "Qwen3-Coder-30B-A3B-Instruct-UD-Q3_K_XL.gguf")


def test_the_warning_rides_on_the_answer_and_never_fails_it(monkeypatch):
    from tools.local_ai import ask as module
    module._props_cache.clear()

    class Reply:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
    monkeypatch.setattr(module.httpx, "post", lambda *a, **k: Reply())
    b = {"name": "local", "model": "qwen3-coder-30b", "base_url": "http://gpu.test:8080/v1", "cloud": False, "key": ""}
    monkeypatch.setattr(module, "_served_model", lambda base: "Qwen3-8B-Q6_K.gguf")
    out = module._call(b, "hi", 10, 0.2, False)
    assert out["text"] == "OK" and out["served_model"] == "Qwen3-8B-Q6_K.gguf" and "asked for qwen3-coder-30b" in out["warning"]
    monkeypatch.setattr(module, "_served_model", lambda base: "")  # can't tell: no warning, the answer still comes
    assert "warning" not in module._call(b, "hi", 10, 0.2, False)
