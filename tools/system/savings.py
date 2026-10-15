"""system.savings. See ../../capabilities/system/savings.md."""
from __future__ import annotations

import os

import httpx

import usage
from registry import tool

# USD per million input tokens: a saved token is one an AI never had to read. The same prices the Control Panel's
# "Show tokens saved as money" setting uses (dashboard/main.py MONEY_MODELS); keep the two in step.
PRICES = {"opus-5-5": ("Claude Opus 5.5", 4.00), "sonnet-5-5": ("Claude Sonnet 5.5", 2.00),
          "haiku-5-5": ("Claude Haiku 5.5", 0.10), "fable-5-1": ("Claude Fable 5.1", 10.00)}
GBP_PER_USD = 0.75  # approximate; the Control Panel uses the day's ECB rate
DAYS = 7


class SavingsError(RuntimeError):
    """A bad argument. A missing source is a null in the answer, not an error."""


def _rtk_daily() -> list[dict] | None:
    """Per-day savings from the command-output filter (RTK) on the helper PC, or None when it isn't set up or can't
    be reached."""
    base = (os.environ.get("GPU_SERVICE_URL") or os.environ.get("LOCAL_AI_DECIDE_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return None
    try:
        r = httpx.get(f"{base}/stats/rtk/daily", timeout=10)
        data = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None
    days = data.get("daily") if isinstance(data, dict) and data.get("available", True) else None
    return days if isinstance(days, list) else None


def _money(tokens: int, model: str) -> dict:
    label, usd_per_m = PRICES[model]
    usd = tokens * usd_per_m / 1_000_000
    return {"usd": round(usd, 2), "gbp": round(usd * GBP_PER_USD, 2), "at": label, "approximate": True}


@tool(name="savings", category="system", doc="system/savings.md")
def savings(model: str = "opus-5-5") -> dict:
    """Tokens saved, today and per day for the last 7 days, with what they'd have cost.

    Args:
        model: the model whose API price turns tokens into money: opus-5-5 (default), sonnet-5-5, haiku-5-5 or
            fable-5-1.

    Returns:
        {"today": {"date", "saved_tokens", "commands", "savings_pct", "money"} or null,
         "days": [{"date", "saved_tokens"}, ...] (oldest first, up to 7; days with no commands are left out),
         "week": {"saved_tokens", "money"}, "tool_server_total_tokens": int (this server's estimate since its usage
         file began), "source": "rtk" | null}
        null where a source isn't set up or can't be reached; money is approximate (a fixed exchange rate).
    """
    if model not in PRICES:
        raise SavingsError(f"model must be one of {', '.join(PRICES)}")
    daily = _rtk_daily()
    rows = [d for d in (daily or []) if isinstance(d, dict) and isinstance(d.get("saved_tokens"), int)][-DAYS:]
    last = rows[-1] if rows else None
    week = sum(d["saved_tokens"] for d in rows)
    today = None
    if last:
        today = {"date": last.get("date"), "saved_tokens": last["saved_tokens"], "commands": last.get("commands"),
                 "savings_pct": round(float(last.get("savings_pct") or 0), 1), "money": _money(last["saved_tokens"], model)}
    return {"today": today, "days": [{"date": d.get("date"), "saved_tokens": d["saved_tokens"]} for d in rows],
            "week": {"saved_tokens": week, "money": _money(week, model)},
            "tool_server_total_tokens": int(usage.summary().get("estimated_tokens_saved") or 0),
            "source": "rtk" if daily is not None else None}
