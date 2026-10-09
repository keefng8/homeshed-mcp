"""llm.chat / llm.providers.list (tools/llm): the owner's switches and daily caps, the host-locked request to xAI's
Responses API, cost and per-client usage, the 5,000-character result, and that the key never shows in a result, an
error or the usage log. Every request goes to a fake API (httpx.MockTransport); the key is a dummy."""
from __future__ import annotations

import json

import httpx
import pytest

import clients
import runtime_settings as rs
import vault
from tools.llm import chat as lc
from tools.llm import providers as prov
from tools.llm import providers_list as pl

KEY = "xai-" + "Dm9" * 12  # secret-scan: allow (a dummy)


def answer(text="Hello from Grok.", status="completed", ticks=1_250_000, **usage):
    u = {"input_tokens": 120, "input_tokens_details": {"cached_tokens": 20}, "output_tokens": 30,
         "output_tokens_details": {"reasoning_tokens": 50}, "total_tokens": 200}
    if ticks is not None:
        u["cost_in_usd_ticks"] = ticks
    u.update(usage)
    return {"id": "r1", "object": "response", "model": "grok-4.3", "status": status, "usage": u,
            "output": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking..."}]},
                       {"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]}]}


class Fake:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.reply = lambda req: httpx.Response(200, json=answer())

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.url.scheme == "https" and req.url.host == "api.x.ai" and req.url.path == "/v1/responses"
        assert req.headers["authorization"] == f"Bearer {KEY}"
        return self.reply(req)

    def body(self, i=-1) -> dict:
        return json.loads(self.calls[i].content)


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
    monkeypatch.setenv("XAI_API_KEY", KEY)
    f = Fake()
    monkeypatch.setattr(prov, "_TRANSPORT", httpx.MockTransport(f))
    f.tmp = tmp_path
    return f


def on(cap="10", tokens="0"):
    rs.update({"llm_provider_xai_enabled": True, "llm_daily_cap_xai": cap, "llm_daily_tokens_xai": tokens})


def ask(**kw):
    kw.setdefault("messages", [{"role": "user", "content": "Say hello"}])
    return lc.chat(**kw)


def blob(x) -> str:
    return json.dumps(x, default=str)


def log_lines(fake) -> list[dict]:
    p = fake.tmp / "llm_usage.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# --- switches ----------------------------------------------------------------------------------------------------

def test_switches_default_off_with_a_cap_of_0_on_the_settings_page():
    assert rs.SCHEMA["llm_provider_xai_enabled"]["default"] is False
    assert rs.SCHEMA["llm_daily_cap_xai"]["default"] == "0"
    assert rs.SCHEMA["llm_daily_tokens_xai"]["default"] == "0"
    assert {rs.SCHEMA[k]["group"] for k in prov.SETTINGS} == {"Language models"}


def test_off_until_the_owner_switches_it_on(fake):
    with pytest.raises(prov.LLMError, match="switched off.*only the owner"):
        ask()
    assert fake.calls == []


def test_unreadable_settings_mean_off(fake, monkeypatch):
    on()
    monkeypatch.setattr(rs, "get", lambda key: (_ for _ in ()).throw(RuntimeError("broken")))
    with pytest.raises(prov.LLMError, match="switched off"):
        ask()
    assert fake.calls == []


def test_a_cap_of_0_refuses_even_when_on(fake):
    rs.update({"llm_provider_xai_enabled": True})
    with pytest.raises(prov.LLMError, match="cap is None"):
        ask()
    assert fake.calls == []


def test_keyless_names_the_entry_never_a_value(fake, monkeypatch):
    on()
    monkeypatch.delenv("XAI_API_KEY")
    with pytest.raises(prov.LLMError, match="XAI_API_KEY") as exc:
        ask()
    assert "isn't configured" in str(exc.value) and fake.calls == []


def test_the_daily_request_cap(fake):
    on(cap="10")
    for _ in range(10):
        ask()
    with pytest.raises(prov.LLMError, match="today's cap of 10 requests"):
        ask()
    assert len(fake.calls) == 10


def test_the_daily_token_cap(fake):
    on(tokens="50000")
    fake.reply = lambda req: httpx.Response(200, json=answer(input_tokens=49_990))
    ask()  # 49,990 + 30 + 50 tokens: over the cap after this one
    with pytest.raises(prov.LLMError, match="cap of 50,000 tokens"):
        ask()
    assert len(fake.calls) == 1


def test_failed_requests_count_toward_the_cap(fake):
    on(cap="10")
    fake.reply = lambda req: httpx.Response(500, json={"error": "boom"})
    for _ in range(10):
        with pytest.raises(prov.LLMError, match="HTTP 500"):
            ask()
    with pytest.raises(prov.LLMError, match="today's cap"):
        ask()


# --- the request and the answer ----------------------------------------------------------------------------------

def test_a_chat_turn(fake):
    on()
    out = ask(messages=[{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"},
                        {"role": "user", "content": "Name a colour"}], system="Be brief.", temperature=0.2,
              max_tokens=200)
    body = fake.body()
    assert body == {"model": "grok-4.3", "max_output_tokens": 200, "store": False, "instructions": "Be brief.",
                    "temperature": 0.2, "input": [{"role": "user", "content": "Hi"},
                                                  {"role": "assistant", "content": "Hello"},
                                                  {"role": "user", "content": "Name a colour"}]}
    assert out["text"] == "Hello from Grok." and out["model"] == "grok-4.3" and out["provider"] == "xai"
    assert out["truncated"] is False and out["finish"] == "completed"
    assert out["usage"] == {"input_tokens": 120, "output_tokens": 30, "reasoning_tokens": 50, "cached_tokens": 20,
                            "cost_usd": 0.000125, "cost_source": "xai"}
    assert out["used_today"] == 1 and out["daily_cap"] == 10


def test_optional_fields_are_left_out(fake):
    on()
    ask()
    body = fake.body()
    assert "instructions" not in body and "temperature" not in body and body["max_output_tokens"] == 1024


def test_cost_is_estimated_when_xai_doesnt_say(fake):
    on()
    fake.reply = lambda req: httpx.Response(200, json=answer(ticks=None))
    u = ask()["usage"]
    # grok-4.3: 100 fresh input at $1.25/M, 20 cached at $0.20/M, 80 output+reasoning at $2.50/M
    assert u["cost_source"] == "estimate" and u["cost_usd"] == pytest.approx((100 * 1.25 + 20 * 0.2 + 80 * 2.5) / 1e6)


def test_another_model(fake):
    on()
    ask(model="grok-4.7")
    assert fake.body()["model"] == "grok-4.7"


def test_long_answers_are_cut_and_flagged_under_5000_characters(fake):
    on()
    fake.reply = lambda req: httpx.Response(200, json=answer(text="word " * 3000))
    out = ask(max_tokens=2048)
    assert out["truncated"] is True and len(out["text"]) == lc.MAX_TEXT_CHARS
    assert len(blob(out)) < 5000


def test_an_incomplete_answer_is_flagged(fake):
    on()
    fake.reply = lambda req: httpx.Response(200, json=answer(text="Partial", status="incomplete"))
    out = ask()
    assert out["truncated"] is True and out["finish"] == "incomplete"


@pytest.mark.parametrize("kw, says", [
    ({"messages": []}, "non-empty list"),
    ({"messages": [{"role": "tool", "content": "x"}]}, "role"),
    ({"messages": [{"role": "user", "content": "  "}]}, "empty content"),
    ({"messages": [{"role": "user"}]}, "role"),
    ({"messages": [{"role": "user", "content": "x"}] * 41}, "at most 40"),
    ({"messages": [{"role": "user", "content": "x" * 48_001}]}, "limit is 48,000"),
    ({"system": "s" * 8001}, "system is over"),
    ({"max_tokens": 0}, "max_tokens"), ({"max_tokens": 2049}, "max_tokens"), ({"max_tokens": True}, "max_tokens"),
    ({"temperature": 2.5}, "temperature"), ({"temperature": -1}, "temperature"),
    ({"model": "gpt-9"}, "no model 'gpt-9'"),
    ({"provider": "openai"}, "unknown provider.*planned: openai"),
])
def test_bad_arguments_are_refused_before_any_request(fake, kw, says):
    on()
    with pytest.raises(prov.LLMError, match=says):
        ask(**kw)
    assert fake.calls == []
    assert prov.today("xai")["requests"] == 0  # a refused argument isn't counted


# --- errors never show the key -----------------------------------------------------------------------------------

def test_errors_are_scrubbed(fake):
    on()
    fake.reply = lambda req: httpx.Response(401, json={"error": f"Incorrect API key {KEY} see https://x.ai/k?t={KEY}"})
    with pytest.raises(prov.LLMError) as exc:
        ask()
    msg = str(exc.value)
    assert "HTTP 401" in msg and "key was refused" in msg and KEY not in msg and "https://" not in msg


def test_redirects_are_refused(fake):
    on()
    fake.reply = lambda req: httpx.Response(307, headers={"location": "https://evil.example/v1/responses"})
    with pytest.raises(prov.LLMError, match="redirects are refused"):
        ask()
    assert len(fake.calls) == 1


def test_timeouts_and_network_errors(fake):
    on()

    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)
    fake.reply = slow
    with pytest.raises(prov.LLMError, match="didn't answer within"):
        ask()

    def down(req):
        raise httpx.ConnectError("down", request=req)
    fake.reply = down
    with pytest.raises(prov.LLMError, match="couldn't reach.*ConnectError"):
        ask()


def test_not_json(fake):
    on()
    fake.reply = lambda req: httpx.Response(200, text="<html>")
    with pytest.raises(prov.LLMError, match="isn't JSON"):
        ask()


def test_the_host_is_fixed():
    assert prov.PROVIDERS["xai"]["host"] == "api.x.ai" and prov.PROVIDERS["xai"]["path"] == "/v1/responses"


# --- usage per client --------------------------------------------------------------------------------------------

def test_usage_is_logged_per_client_without_text_or_key(fake):
    on()
    token = clients.current_client.set("my-app")
    try:
        ask(messages=[{"role": "user", "content": "secret plans for the shop"}])
    finally:
        clients.current_client.reset(token)
    ask()  # the owner
    fake.reply = lambda req: httpx.Response(500, json={"error": "boom"})
    with pytest.raises(prov.LLMError):
        ask()
    lines = log_lines(fake)
    assert [(x["client"], x["ok"]) for x in lines] == [("my-app", True), ("owner", True), ("owner", False)]
    assert lines[0]["model"] == "grok-4.3" and lines[0]["input_tokens"] == 120 and lines[0]["cost_usd"] == 0.000125
    assert lines[0]["provider"] == "xai" and lines[0]["ts"] and "HTTP 500" in lines[2]["error"]
    raw = (fake.tmp / "llm_usage.jsonl").read_text() + (fake.tmp / "llm_daily.json").read_text()
    assert "secret plans" not in raw and "Hello from Grok" not in raw and KEY not in raw
    row = next(p for p in pl.providers_list()["providers"] if p["provider"] == "xai")
    assert row["used_today"] == 3 and row["by_client_today"]["my-app"]["requests"] == 1
    assert row["by_client_today"]["owner"]["requests"] == 2 and row["cost_today_usd"] == pytest.approx(0.00025)


def test_the_log_rotates(fake, monkeypatch):
    on()
    monkeypatch.setattr(prov, "LOG_MAX_BYTES", 100)
    ask()
    ask()
    assert (fake.tmp / "llm_usage.jsonl.1").exists() and len(log_lines(fake)) == 1


def test_a_full_disk_doesnt_lose_the_answer(fake, monkeypatch):
    on()
    monkeypatch.setenv("LLM_USAGE_LOG", str(fake.tmp))  # a folder: appending to it fails
    assert ask()["text"] == "Hello from Grok."


def test_a_new_day_starts_at_zero(fake, monkeypatch):
    on(cap="10")
    for _ in range(10):
        ask()
    monkeypatch.setattr(prov, "_today", lambda: "2099-01-01")
    assert ask()["used_today"] == 1


# --- llm.providers.list ------------------------------------------------------------------------------------------

def test_providers_list_when_off(fake):
    out = pl.providers_list()
    row = out["providers"][0]
    assert row["provider"] == "xai" and row["enabled"] is False and row["usable"] is False
    assert row["configured"] is True and row["key_name"] == "XAI_API_KEY" and "switched off" in row["why_not"]
    assert row["daily_cap"] == 0 and row["used_today"] == 0 and row["daily_token_cap"] is None
    assert row["default_model"] == "grok-4.3" and {"model": "grok-4.3"}.items() <= row["models"][0].items()
    assert out["planned"] == ["openai", "anthropic"] and KEY not in blob(out) and len(blob(out)) < 5000
    assert fake.calls == []


def test_providers_list_when_usable(fake):
    on(cap="25", tokens="100000")
    row = pl.providers_list()["providers"][0]
    assert row["usable"] is True and "why_not" not in row and row["daily_cap"] == 25
    assert row["daily_token_cap"] == 100000


def test_providers_list_keyless(fake, monkeypatch):
    on()
    monkeypatch.delenv("XAI_API_KEY")
    row = pl.providers_list()["providers"][0]
    assert row["configured"] is False and "XAI_API_KEY" in row["why_not"]


def test_settings_reject_values_off_the_list():
    with pytest.raises(ValueError):
        rs.validate("llm_daily_cap_xai", "7")
    with pytest.raises(ValueError):
        rs.validate("llm_provider_xai_enabled", "yes")
