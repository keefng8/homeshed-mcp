"""llm.chat with provider "anthropic": the Messages API request (x-api-key, system folded in, images as base64
blocks), cost from the price table, and the owner's free monthly credit (2026-10-22) as a hard stop. Every request
goes to a fake API (httpx.MockTransport); the key is a dummy."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

import runtime_settings as rs
import vault
from tools.llm import chat as lc
from tools.llm import providers as prov
from tools.llm import providers_list as pl

KEY = "sk-ant-" + "Qx7" * 12  # secret-scan: allow (a dummy)


def answer(text="Hello from Claude.", stop="end_turn", **usage):
    u = {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    u.update(usage)
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5", "stop_reason": stop,
            "content": [{"type": "text", "text": text}], "usage": u}


class Fake:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.reply = lambda req: httpx.Response(200, json=answer())

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.url.host == "api.anthropic.com" and req.url.path == "/v1/messages"
        assert req.headers["x-api-key"] == KEY and req.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in req.headers  # the key goes in one header only
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
    monkeypatch.setenv("CLAUDE_WORKSPACE_API", KEY)
    f = Fake()
    monkeypatch.setattr(prov, "_TRANSPORT", httpx.MockTransport(f))
    rs.update({"llm_provider_anthropic_enabled": True, "llm_daily_cap_anthropic": "100",
               "llm_monthly_usd_anthropic": "100"})
    return f


def ask(**kw):
    kw.setdefault("messages", [{"role": "user", "content": "Say hello"}])
    return lc.chat(provider="anthropic", **kw)


def test_a_chat_turn_with_cost_and_credit_left(fake):
    out = ask(system="Be brief.", messages=[{"role": "system", "content": "No prices."},
                                           {"role": "user", "content": "Say hello"}], temperature=0.2)
    b = fake.body()
    assert b["model"] == "claude-haiku-5-5"
    assert b["system"] == [{"type": "text", "text": "Be brief.\n\nNo prices.", "cache_control": {"type": "ephemeral"}}]
    assert b["cache_control"] == {"type": "ephemeral"}  # automatic caching for a growing conversation
    assert b["messages"] == [{"role": "user", "content": "Say hello"}]
    assert out["text"] == "Hello from Claude." and out["truncated"] is False
    assert out["usage"]["cost_usd"] == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)
    assert out["credit_left_usd"] == pytest.approx(100 - out["usage"]["cost_usd"], abs=1e-4)
    assert KEY not in json.dumps(out)


def test_cached_tokens_are_priced_as_cache_reads(fake):
    fake.reply = lambda req: httpx.Response(200, json=answer(input_tokens=100, cache_read_input_tokens=900))
    u = ask()["usage"]
    assert u["input_tokens"] == 1000 and u["cached_tokens"] == 900
    assert u["cost_usd"] == pytest.approx((100 * 0.10 + 900 * 0.01 + 200 * 0.50) / 1e6)


def test_cache_writes_cost_a_quarter_more(fake):
    fake.reply = lambda req: httpx.Response(200, json=answer(input_tokens=100, cache_creation_input_tokens=900))
    u = ask()["usage"]
    assert u["input_tokens"] == 1000 and u["cached_tokens"] == 0
    assert u["cost_usd"] == pytest.approx((100 * 0.10 + 900 * 0.10 * 1.25 + 200 * 0.50) / 1e6, abs=1e-6)


def test_max_tokens_stop_is_flagged(fake):
    fake.reply = lambda req: httpx.Response(200, json=answer(stop="max_tokens"))
    assert ask()["truncated"] is True


def test_images_become_base64_blocks(fake, monkeypatch):
    from tools.image import refs
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    monkeypatch.setattr(refs, "image_bytes", lambda ref, max_bytes=0: png)
    ask(messages=[{"role": "user", "content": "Is a child visible?"}], images=["hero.jpg"])
    parts = fake.body()["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "Is a child visible?"}
    assert parts[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.b64encode(png).decode()}}


def test_temperature_is_not_sent(fake):
    ask(temperature=1.5)  # the Claude 5.5 models refuse it (HTTP 400, seen live 2026-10-22)
    assert "temperature" not in fake.body()


def test_the_monthly_credit_is_a_hard_stop(fake):
    prov._add_month_cost("anthropic", 100.0)
    with pytest.raises(prov.LLMError, match="this month's \\$100 spend limit"):
        ask()
    assert fake.calls == []
    row = next(r for r in pl.providers_list()["providers"] if r["provider"] == "anthropic")
    assert row["credit_left_usd"] == 0 and row["cost_month_usd"] == 100 and row["usable"] is False
    rs.update({"llm_monthly_usd_anthropic": "250"})  # the owner raises it: usable again at once
    assert ask()["credit_left_usd"] == pytest.approx(150, abs=0.01)


def test_the_monthly_limit_is_the_owners_setting_and_off_by_default(fake):
    """The owner, 2026-10-22: a user-set monthly stop, not a hard-coded $100. A new install has none."""
    assert rs.SCHEMA["llm_monthly_usd_anthropic"]["default"] == "0"
    assert rs.SCHEMA["llm_monthly_usd_xai"]["group"] == "Language models"  # every provider gets one
    rs.update({"llm_monthly_usd_anthropic": "0"})
    prov._add_month_cost("anthropic", 1000.0)
    out = ask()
    assert out["text"] == "Hello from Claude." and "credit_left_usd" not in out
    row = next(r for r in pl.providers_list()["providers"] if r["provider"] == "anthropic")
    assert row["monthly_credit_usd"] is None and row["credit_left_usd"] is None
    with pytest.raises(ValueError):
        rs.validate("llm_monthly_usd_anthropic", "7")


def test_a_new_month_starts_at_zero(fake, monkeypatch):
    prov._add_month_cost("anthropic", 100.0)
    monkeypatch.setattr(prov.time, "strftime", lambda fmt, *a: "2026-11" if fmt == "%Y-%m" else "2026-11-01")
    assert prov.month_cost("anthropic") == 0.0


def test_errors_are_scrubbed(fake):
    fake.reply = lambda req: httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                                  "message": f"bad key {KEY}"}})
    with pytest.raises(prov.LLMError) as e:
        ask()
    assert "401" in str(e.value) and KEY not in str(e.value)
