"""The language-model relay behind llm.chat and llm.providers.list. See ../../capabilities/llm/relay.md.

The point (2026-10-04): an app (for example one on another machine) may think with a paid model (xAI's Grok first), but never holds the key.
It calls llm.chat with its own client token; the key is read here, server-side, by name (vault.secret: the Control
Panel's Credentials first, then the environment) and only ever put in one request header.

Switches (Control Panel > Settings > AI and tools > Language models; runtime_settings in usage/settings.json, owner
token only, live): per provider an on/off switch (default OFF), a daily request cap (default 0: no use at all) and an
optional daily token cap (default 0: no token limit, the request cap still applies). A provider is usable only when
it's switched on, its key is stored and today's caps aren't reached. Clients see this through llm.providers.list
and can't change it (client tokens only reach /mcp).

Every request goes to the provider's own host over https (api.x.ai), redirects refused, with timeouts and a size cap.
Errors carry the HTTP status and a short reason with URLs and the key scrubbed out. Requests are counted when they
start (so failed ones count too); tokens and cost when they finish. The usage log keeps who (client), provider, model,
tokens and cost, never a prompt or an answer.

Adding a provider (openai, anthropic): a PROVIDERS entry (key name, host, models with prices) and an adapter in
ADAPTERS that takes (key, model, messages, system, max_tokens, temperature) and returns the common result.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from paths import data_path

# Prices per 1M tokens in USD, from docs.x.ai/docs/models (checked 2026-10-04; the < 200k-prompt rows: llm.chat's input
# cap keeps every request far below 200k tokens). xAI also reports the exact cost per request (usage.cost_in_usd_ticks),
# which is used when present; these are the fallback estimate.
PROVIDERS: dict[str, dict] = {
    "xai": {
        "label": "xAI Grok", "key": "XAI_API_KEY", "host": "api.x.ai", "path": "/v1/responses",
        "pricing_url": "https://docs.x.ai/docs/models", "default_model": "grok-4.3",
        "models": {
            "grok-4.3": {"in": 1.25, "cached": 0.20, "out": 2.50,
                         "note": "fast and cheapest general model; reasoning effort low by default"},
            "grok-4.7": {"in": 2.00, "cached": 0.50, "out": 6.00, "note": "xAI's most capable model"},
            "grok-4.6": {"in": 2.00, "cached": 0.50, "out": 6.00},
            "grok-4.5": {"in": 2.00, "cached": 0.30, "out": 6.00},
            "grok-4.20-0309-non-reasoning": {"in": 1.25, "cached": 0.20, "out": 2.50},
            "grok-4.20-0309-reasoning": {"in": 1.25, "cached": 0.20, "out": 2.50},
            "grok-build-0.1": {"in": 1.00, "cached": 0.20, "out": 2.00, "note": "coding"},
        },
    },
}
PLANNED = ["openai", "anthropic"]  # same switches and caps when added; not callable yet

SETTINGS_GROUP = "Language models"
CAP_OPTIONS = ["0", "10", "25", "50", "100", "250", "500", "1000"]
TOKEN_CAP_OPTIONS = ["0", "50000", "100000", "250000", "500000", "1000000", "2000000"]
DAILY_FILE_DEFAULT = "usage/llm_daily.json"
LOG_FILE_DEFAULT = "usage/llm_usage.jsonl"
LOG_MAX_BYTES = 2 * 1024 * 1024  # then rotated once to .1

TIMEOUT_S = 90.0
TIMEOUT = httpx.Timeout(TIMEOUT_S, connect=5.0)
MAX_BYTES = 2 * 1024 * 1024
OWNER_HINT = "only the owner can change this: Control Panel > Settings > AI and tools > Language models"
_URL = re.compile(r"\b(?:https?|data):\S+", re.IGNORECASE)

# Tests set this to an httpx.MockTransport so no request ever leaves the process.
_TRANSPORT: httpx.BaseTransport | None = None
_lock = threading.Lock()


class LLMError(RuntimeError):
    """Safe to show: no key, no URL."""


# --- switches (runtime_settings entries; the Settings page draws them) ---------------------------------------------

def enabled_setting(provider: str) -> str:
    return f"llm_provider_{provider}_enabled"


def cap_setting(provider: str) -> str:
    return f"llm_daily_cap_{provider}"


def token_cap_setting(provider: str) -> str:
    return f"llm_daily_tokens_{provider}"


def _build_settings() -> dict:
    out: dict = {}
    for name, p in PROVIDERS.items():
        out[enabled_setting(name)] = {
            "type": "bool", "default": False, "group": SETTINGS_GROUP,
            "label": f"Let apps use {p['label']} (paid)",
            "help": f"llm.chat through HomeShed, so your apps never hold the key. Paid per "
                    f"token. Also needs {p['key']} under Keys and passwords, and a daily cap above None. "
                    "Off until you switch it on.",
        }
        out[cap_setting(name)] = {
            "type": "select", "default": "0", "group": SETTINGS_GROUP, "options": CAP_OPTIONS,
            "option_labels": {o: ("None" if o == "0" else f"{o} a day") for o in CAP_OPTIONS},
            "label": f"{p['label']}: most requests a day",
            "help": "Requests allowed per day, for every app together (counted when a request starts, so failed ones "
                    "count too). None means it can't be used at all.",
        }
        out[token_cap_setting(name)] = {
            "type": "select", "default": "0", "group": SETTINGS_GROUP, "options": TOKEN_CAP_OPTIONS,
            "option_labels": {o: ("No token limit" if o == "0" else f"{int(o):,} tokens a day")
                              for o in TOKEN_CAP_OPTIONS},
            "label": f"{p['label']}: most tokens a day",
            "help": "Input plus output tokens allowed per day, a second limit on spend. No token limit leaves only "
                    "the request cap above.",
        }
    return out


SETTINGS = _build_settings()


def _setting(key: str):
    """The live value (runtime_settings re-reads its file when it changes); a broken store means the default (off)."""
    try:
        import runtime_settings
        return runtime_settings.get(key) if key in runtime_settings.SCHEMA else SETTINGS[key]["default"]
    except Exception:  # noqa: BLE001 - an unreadable settings store never switches a paid provider on
        return SETTINGS[key]["default"]


def _int_setting(key: str) -> int:
    try:
        return max(0, int(_setting(key)))
    except (TypeError, ValueError):
        return 0


def provider_enabled(provider: str) -> bool:
    return _setting(enabled_setting(provider)) is True


def daily_cap(provider: str) -> int:
    return _int_setting(cap_setting(provider))


def daily_token_cap(provider: str) -> int | None:
    """Tokens allowed today, or None for no token limit."""
    return _int_setting(token_cap_setting(provider)) or None


# --- today's counts and the usage log ------------------------------------------------------------------------------

def _daily_file() -> Path:
    return Path(os.environ.get("LLM_USAGE_FILE") or data_path(DAILY_FILE_DEFAULT))


def _log_file() -> Path:
    return Path(os.environ.get("LLM_USAGE_LOG") or data_path(LOG_FILE_DEFAULT))


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _daily() -> dict:
    try:
        data = json.loads(_daily_file().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("date") == _today() and isinstance(data.get("providers"), dict):
            data.setdefault("clients", {})
            return data
    except (OSError, ValueError):
        pass
    return {"date": _today(), "providers": {}, "clients": {}}


def _write_daily(data: dict) -> None:
    f = _daily_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, f)


def _row(d: dict, k: str) -> dict:
    r = d.setdefault(k, {})
    for f in ("requests", "tokens"):
        r[f] = int(r.get(f, 0) or 0)
    r["cost_usd"] = float(r.get("cost_usd", 0.0) or 0.0)
    return r


def today(provider: str) -> dict:
    """{"requests", "tokens", "cost_usd"} so far today for provider."""
    return dict(_row(_daily()["providers"], provider))


def clients_today(provider: str) -> dict:
    return {c: dict(_row(v, provider)) for c, v in _daily()["clients"].items() if provider in v}


def unusable_reason(provider: str, hint: bool = True) -> str | None:
    """Why provider can't be used right now, or None if it can. Never mentions a key's value."""
    if provider not in PROVIDERS:
        raise LLMError(f"unknown provider {provider!r}; available: {sorted(PROVIDERS)}"
                       + (f" (planned: {', '.join(PLANNED)})" if PLANNED else ""))
    p, tail = PROVIDERS[provider], (f" ({OWNER_HINT})" if hint else "")
    if not provider_enabled(provider):
        return f"{provider} is switched off{tail}"
    if not configured(provider):
        return f"{provider} isn't configured: add {p['key']} under the Control Panel's Keys and passwords"
    cap, used = daily_cap(provider), today(provider)
    if cap == 0:
        return f"{provider}'s daily request cap is None (0), so it can't be used{tail}"
    if used["requests"] >= cap:
        return f"{provider} has reached today's cap of {cap} requests{tail}"
    tcap = daily_token_cap(provider)
    if tcap is not None and used["tokens"] >= tcap:
        return f"{provider} has reached today's cap of {tcap:,} tokens{tail}"
    return None


def reserve(provider: str, client: str) -> None:
    """Check the switches and caps and count one request, atomically (two calls can't both take the last one)."""
    with _lock:
        why = unusable_reason(provider)
        if why:
            raise LLMError(why)
        data = _daily()
        _row(data["providers"], provider)["requests"] += 1
        _row(data["clients"].setdefault(client, {}), provider)["requests"] += 1
        _write_daily(data)


def record(provider: str, client: str, model: str, usage: dict | None, ok: bool, error: str | None = None) -> None:
    """Add a finished request's tokens and cost to today's counts and append one line to the usage log. Never raises:
    a full disk mustn't turn a paid, answered request into an error."""
    u = usage or {}
    tokens = int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0) + int(u.get("reasoning_tokens") or 0)
    cost = float(u.get("cost_usd") or 0.0)
    try:
        with _lock:
            data = _daily()
            for r in (_row(data["providers"], provider), _row(data["clients"].setdefault(client, {}), provider)):
                r["tokens"] += tokens
                r["cost_usd"] = round(r["cost_usd"] + cost, 6)
            _write_daily(data)
            line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "client": client, "provider": provider,
                    "model": model, "ok": ok, "input_tokens": u.get("input_tokens"),
                    "output_tokens": u.get("output_tokens"), "reasoning_tokens": u.get("reasoning_tokens"),
                    "cached_tokens": u.get("cached_tokens"), "cost_usd": u.get("cost_usd"),
                    "cost_source": u.get("cost_source")}
            if error:
                line["error"] = error[:120]
            f = _log_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            if f.exists() and f.stat().st_size > LOG_MAX_BYTES:
                os.replace(f, f.with_suffix(f.suffix + ".1"))
            with f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
    except (OSError, ValueError):
        pass


# --- keys and the host-locked request ------------------------------------------------------------------------------

def _secret(name: str) -> str | None:
    import vault  # stored credentials first, then the environment (vault.py)
    value = vault.secret(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def configured(provider: str) -> bool:
    return bool(_secret(PROVIDERS[provider]["key"]))


def clean(text: Any, secrets: tuple[str, ...] = ()) -> str:
    out = _URL.sub("<url>", str(text or ""))
    for s in secrets:
        if s and len(s) >= 4:
            out = out.replace(s, "<secret>")
    return re.sub(r"\s+", " ", out).strip()[:200]


def _reason(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    err = body.get("error")
    if isinstance(err, dict):
        err = err.get("message") or err.get("type") or ""
    return str(err or body.get("message") or body.get("detail") or body.get("code") or "")


HINTS = {400: " (the request was refused: check the model and messages)", 401: " (the key was refused)",
         402: " (out of credit?)", 403: " (the key lacks access, or the account has no credit)",
         404: " (unknown model or endpoint)", 429: " (rate limited or out of quota: try again later)"}


def post_json(provider: str, key: str, body: dict) -> dict:
    """One POST to the provider's own host and path. The key goes in the Authorization header only."""
    p = PROVIDERS[provider]
    host, name = p["host"], p["label"]
    kwargs: dict = {"timeout": TIMEOUT, "follow_redirects": False}
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}
    try:
        with httpx.Client(**kwargs) as http:
            resp = http.post(f"https://{host}{p['path']}", json=body, headers=headers)
    except httpx.TimeoutException:
        raise LLMError(f"{name} didn't answer within {TIMEOUT_S:.0f} seconds") from None
    except httpx.HTTPError as exc:
        raise LLMError(f"couldn't reach {name} ({type(exc).__name__})") from None
    if resp.url.host != host:  # belt and braces: the client never follows redirects
        raise LLMError(f"{name} answered from an unexpected host; ignored")
    if 300 <= resp.status_code < 400:
        raise LLMError(f"{name} tried to redirect (HTTP {resp.status_code}); redirects are refused")
    if resp.status_code >= 400:
        reason = clean(_reason(resp), (key,))
        raise LLMError(f"{name} answered HTTP {resp.status_code}{HINTS.get(resp.status_code, '')}"
                       + (f": {reason}" if reason else ""))
    if len(resp.content) > MAX_BYTES:
        raise LLMError(f"{name}'s answer is over {MAX_BYTES // 2**20} MB; refused")
    try:
        out = resp.json()
    except ValueError:
        raise LLMError(f"{name} sent an answer that isn't JSON") from None
    if not isinstance(out, dict):
        raise LLMError(f"{name} sent an unexpected answer")
    return out


# --- adapters ------------------------------------------------------------------------------------------------------

def estimate_cost(provider: str, model: str, input_tokens: int, cached: int, output_tokens: int) -> float | None:
    price = PROVIDERS[provider]["models"].get(model)
    if not price:
        return None
    fresh = max(0, input_tokens - cached)
    return round((fresh * price["in"] + cached * price["cached"] + output_tokens * price["out"]) / 1_000_000, 6)


def _xai(key: str, model: str, messages: list[dict], system: str, max_tokens: int,
         temperature: float | None) -> dict:
    """xAI's Responses API (POST /v1/responses, docs.x.ai/docs/api-reference, 2026-10-04). store=false: xAI keeps
    nothing for later retrieval. max_output_tokens bounds the visible answer; reasoning tokens are billed on top."""
    body: dict = {"model": model, "input": messages, "max_output_tokens": max_tokens, "store": False}
    if system:
        body["instructions"] = system
    if temperature is not None:
        body["temperature"] = temperature
    d = post_json("xai", key, body)
    if isinstance(d.get("error"), dict) and d["error"].get("message"):
        raise LLMError(f"xAI Grok reported an error: {clean(d['error']['message'], (key,))}")
    parts = []
    for item in d.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "output_text" and isinstance(c.get("text"), str):
                    parts.append(c["text"])
    u = d.get("usage") if isinstance(d.get("usage"), dict) else {}
    in_t = int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
    out_t = int(u.get("output_tokens") or u.get("completion_tokens") or 0)
    details_in = u.get("input_tokens_details") or u.get("prompt_tokens_details") or {}
    details_out = u.get("output_tokens_details") or u.get("completion_tokens_details") or {}
    cached = int(details_in.get("cached_tokens") or 0) if isinstance(details_in, dict) else 0
    reasoning = int(details_out.get("reasoning_tokens") or 0) if isinstance(details_out, dict) else 0
    ticks = u.get("cost_in_usd_ticks")
    if isinstance(ticks, (int, float)) and ticks >= 0:  # 10,000,000,000 ticks to the dollar (xAI's docs)
        cost, source = round(ticks / 1e10, 6), "xai"
    else:
        cost, source = estimate_cost("xai", model, in_t, cached, out_t + reasoning), "estimate"
    status = d.get("status") or "completed"
    return {"text": "".join(parts), "model": str(d.get("model") or model)[:60], "finish": str(status)[:20],
            "incomplete": status == "incomplete",
            "usage": {"input_tokens": in_t, "output_tokens": out_t, "reasoning_tokens": reasoning,
                      "cached_tokens": cached, "cost_usd": cost, "cost_source": source if cost is not None else None}}


ADAPTERS = {"xai": _xai}


def call(provider: str, model: str, messages: list[dict], system: str, max_tokens: int,
         temperature: float | None) -> dict:
    p = PROVIDERS[provider]
    key = _secret(p["key"])
    if not key:
        raise LLMError(f"{provider} isn't configured: add {p['key']} under the Control Panel's Keys and passwords")
    try:
        return ADAPTERS[provider](key, model, messages, system, max_tokens, temperature)
    except LLMError as exc:  # every message is scrubbed already; this is belt and braces
        raise LLMError(str(exc).replace(key, "<secret>")) from None


def catalogue() -> list[dict]:
    rows = []
    for name, p in PROVIDERS.items():
        why, used = unusable_reason(name, hint=False), today(name)
        row = {"provider": name, "label": p["label"], "enabled": provider_enabled(name), "configured": configured(name),
               "usable": why is None, "key_name": p["key"], "daily_cap": daily_cap(name),
               "used_today": used["requests"], "daily_token_cap": daily_token_cap(name),
               "tokens_today": used["tokens"], "cost_today_usd": round(used["cost_usd"], 6),
               "by_client_today": {c: {"requests": v["requests"], "tokens": v["tokens"],
                                       "cost_usd": round(v["cost_usd"], 6)}
                                   for c, v in sorted(clients_today(name).items())[:20]},
               "default_model": p["default_model"], "pricing_url": p["pricing_url"],
               "models": [{"model": m, "usd_per_1m": {"input": v["in"], "cached_input": v["cached"],
                                                      "output": v["out"]}, **({"note": v["note"]} if v.get("note")
                                                                              else {})}
                          for m, v in p["models"].items()]}
        if why:
            row["why_not"] = why
        rows.append(row)
    return rows
