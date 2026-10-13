"""Paid spend per app per day, and each app's daily cap (2026-10-10: each app or bot gets its own client; the owner
sets a dollar cap per app on the API page, so a bot can't run up the paid image and language-model bills). See capabilities/spend/today.md.

What counts: paid image jobs (their estimated price, counted when the job starts and refunded when the provider made
nothing) and paid language-model calls (the provider's own cost when it says, else the price list's estimate, counted
when the answer comes back). Free and local work is never counted and never capped. The owner's own calls (no client)
are counted but never capped.

Dollars, because every provider prices in dollars. Days are UK days (the server's clock), like every other daily cap.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from paths import data_path

FILE = Path(os.environ.get("SPEND_FILE") or data_path("usage/spend_by_client.json"))
KEEP_DAYS = 31
UNKNOWN_IMAGE_USD = 0.20  # a model whose price isn't listed counts as this, so a cap still holds
CAP_REACHED = "SPEND_CAP_REACHED"  # in every refusal, so an app can spot it and pause the bot
KINDS = ("image", "llm")
_lock = threading.Lock()


class SpendCapReached(RuntimeError):
    """The app has spent its daily cap: its paid calls are refused until tomorrow."""


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"days": {}}


def _save(data: dict) -> None:
    days = data["days"]
    for old in sorted(days)[:-KEEP_DAYS]:
        del days[old]
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, FILE)


def spent(client: str, day: str | None = None) -> dict:
    """{"image", "llm", "total"} in dollars for one app on one day (today by default)."""
    row = _load()["days"].get(day or _today(), {}).get(client or "owner", {})
    out = {k: round(float(row.get(k, 0.0) or 0.0), 6) for k in KINDS}
    out["total"] = round(sum(out.values()), 6)
    return out


def cap_of(client: str) -> float | None:
    """The app's daily cap in dollars, or None for no cap (and always None for the owner)."""
    if not client or client == "owner":
        return None
    try:
        import clients
        c = (clients._load().get("clients") or {}).get(client) or {}
    except Exception:  # noqa: BLE001 - an unreadable client list never blocks: the provider caps still hold
        return None
    v = c.get("daily_spend_usd")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def check(client: str, kind: str, about_usd: float = 0.0) -> None:
    """Refuse a paid call that would take the app past its cap (for a language-model call, whose price is only known
    afterwards, once the app has reached it)."""
    cap = cap_of(client)
    if cap is None:
        return
    now = spent(client)["total"]
    if now >= cap or (about_usd > 0 and now + about_usd > cap + 1e-9):
        raise SpendCapReached(
            f"{CAP_REACHED}: {client} has spent ${now:.2f} of its ${cap:.2f} daily cap on paid images and language "
            f"models, so this paid {'image' if kind == 'image' else 'model call'}"
            + (f" (about ${about_usd:.2f})" if about_usd > 0 else "") + " is refused until tomorrow. Free and local "
            "tools still work. Only the owner changes the cap: Control Panel > API access > Spend cap.")


def add(client: str, kind: str, usd: float | None) -> None:
    """Count (or, negative, refund) spend. Never raises: a full disk mustn't turn a paid, answered call into an error."""
    if not usd or kind not in KINDS:
        return
    try:
        with _lock:
            data = _load()
            row = data["days"].setdefault(_today(), {}).setdefault(client or "owner", {})
            row[kind] = round(max(0.0, float(row.get(kind, 0.0) or 0.0) + float(usd)), 6)
            _save(data)
    except (OSError, ValueError, TypeError):
        pass


def today_all() -> list[dict]:
    """Every app that spent today or has a cap: spend, cap, what's left and whether it's capped now."""
    names = set(_load()["days"].get(_today(), {}))
    try:
        import clients
        names |= {n for n, c in (clients._load().get("clients") or {}).items() if c.get("daily_spend_usd") is not None}
    except Exception:  # noqa: BLE001
        pass
    rows = []
    for name in sorted(names):
        s, cap = spent(name), cap_of(name)
        rows.append({"client": name, **s, "cap_usd": cap,
                     "left_usd": None if cap is None else round(max(0.0, cap - s["total"]), 4),
                     "capped": cap is not None and s["total"] >= cap})
    return rows
