"""llm.chat with provider "openai" (the Responses API, like xAI) and "gemini" (Google's OpenAI-compatible Chat
Completions), added 2026-10-23 with the longer limits for chapter batches. Every request goes to a fake API
(httpx.MockTransport); the keys are dummies."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

import runtime_settings as rs
import vault
from tools.llm import chat as lc
from tools.llm import providers as prov

OPENAI_KEY = "sk-" + "Zr4" * 14  # secret-scan: allow (a dummy)
GEMINI_KEY = "AIza" + "Pq9" * 12  # secret-scan: allow (a dummy)
HOSTS = {"api.openai.com": ("/v1/responses", OPENAI_KEY),
         "generativelanguage.googleapis.com": ("/v1beta/openai/chat/completions", GEMINI_KEY)}


def responses_answer(text="Hello from OpenAI.", status="completed"):
    return {"model": "gpt-5.4-mini", "status": status,
            "output": [{"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 1000, "output_tokens": 200, "input_tokens_details": {"cached_tokens": 400},
                      "output_tokens_details": {"reasoning_tokens": 50}}}


def chat_answer(text="Hello from Gemini.", finish="stop"):
    return {"model": "gemini-3.8-flash", "choices": [{"message": {"role": "assistant", "content": text},
                                                      "finish_reason": finish}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200,
                      "completion_tokens_details": {"reasoning_tokens": 100}}}


class Fake:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.timeouts: list = []
        self.reply = lambda req: httpx.Response(
            200, json=responses_answer() if req.url.host == "api.openai.com" else chat_answer())

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        self.timeouts.append(req.extensions.get("timeout"))
        path, key = HOSTS[req.url.host]
        assert req.url.path == path and req.headers["authorization"] == f"Bearer {key}"
        return self.reply(req)

    def body(self) -> dict:
        return json.loads(self.calls[-1].content)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setattr(vault, "KEY_FILE", tmp_path / "no.key")
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault.json")
    monkeypatch.setattr(vault, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(vault, "_cache", (None, None))
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    monkeypatch.setenv("LLM_USAGE_FILE", str(tmp_path / "llm_daily.json"))
    monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "llm_usage.jsonl"))
    monkeypatch.setenv("LLM_MONTHLY_FILE", str(tmp_path / "llm_monthly.json"))
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    f = Fake()
    monkeypatch.setattr(prov, "_TRANSPORT", httpx.MockTransport(f))
    rs.update({"llm_provider_openai_enabled": True, "llm_daily_cap_openai": "100",
               "llm_provider_gemini_enabled": True, "llm_daily_cap_gemini": "100"})
    return f


def ask(provider, **kw):
    kw.setdefault("messages", [{"role": "user", "content": "Say hello"}])
    return lc.chat(provider=provider, **kw)


def test_openai_uses_the_responses_api_and_prices_cached_and_reasoning_tokens(fake):
    out = ask("openai", system="Be brief.", temperature=0.3)
    b = fake.body()
    assert b["model"] == "gpt-5.4-mini" and b["instructions"] == "Be brief." and b["store"] is False
    assert "temperature" not in b  # its reasoning models refuse it
    assert out["text"] == "Hello from OpenAI." and out["truncated"] is False
    u = out["usage"]
    assert (u["input_tokens"], u["cached_tokens"], u["reasoning_tokens"]) == (1000, 400, 50)
    assert u["cost_usd"] == pytest.approx((600 * 0.75 + 400 * 0.075 + 250 * 4.50) / 1e6)
    assert u["cost_source"] == "estimate" and OPENAI_KEY not in json.dumps(out)


def test_gemini_uses_chat_completions_with_the_system_message_first(fake):
    out = ask("gemini", system="Be brief.", temperature=0.3, model="gemini-3.5-flash-lite")
    b = fake.body()
    assert b["messages"][0] == {"role": "system", "content": "Be brief."} and b["temperature"] == 0.3
    assert b["model"] == "gemini-3.5-flash-lite" and b["max_tokens"] == 1024
    assert out["text"] == "Hello from Gemini."
    assert out["usage"]["cost_usd"] == pytest.approx((1000 * 0.30 + 300 * 2.50) / 1e6)


def test_gemini_length_stop_is_flagged(fake):
    fake.reply = lambda req: httpx.Response(200, json=chat_answer(finish="length"))
    assert ask("gemini")["truncated"] is True


def test_gemini_images_become_image_url_parts(fake, monkeypatch):
    from tools.image import refs
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    monkeypatch.setattr(refs, "image_bytes", lambda ref, max_bytes=0: png)
    ask("gemini", messages=[{"role": "user", "content": "What's shown?"}], images=["hero.png"])
    parts = fake.body()["messages"][0]["content"]
    assert parts == [{"type": "text", "text": "What's shown?"},
                     {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]


def test_a_long_answer_waits_longer(fake):
    ask("openai", max_tokens=16_000)
    assert fake.timeouts[-1]["read"] == pytest.approx(prov.timeout_for(16_000))
    assert prov.timeout_for(1024) == 90 and prov.timeout_for(8_000) == pytest.approx(90 + 5_952 / 25)
    assert prov.timeout_for(16_000) == prov.MAX_TIMEOUT_S == 600


def test_each_new_provider_is_off_until_the_owner_switches_it_on(fake):
    for p in ("openai", "gemini"):
        assert rs.SCHEMA[f"llm_provider_{p}_enabled"]["default"] is False
        assert rs.SCHEMA[f"llm_daily_cap_{p}"]["default"] == "0"
    rs.update({"llm_provider_gemini_enabled": False})
    with pytest.raises(prov.LLMError, match="switched off"):
        ask("gemini")
    assert fake.calls == []


def test_errors_are_scrubbed(fake):
    fake.reply = lambda req: httpx.Response(401, json={"error": {"message": f"bad key {GEMINI_KEY}"}})
    with pytest.raises(prov.LLMError) as e:
        ask("gemini")
    assert "401" in str(e.value) and GEMINI_KEY not in str(e.value)
