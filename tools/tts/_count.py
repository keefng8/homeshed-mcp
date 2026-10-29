"""The month's character count for tts.synthesize, the cap, and calls per app. See ../../capabilities/tts/usage.md.

Google bills text to speech by characters a calendar month, spaces and new lines included, and its billing months
run on US Pacific time (docs.cloud.google.com/billing/docs/how-to/reports: days start "at midnight US and Canadian
Pacific Time"; docs.cloud.google.com/identity/docs/how-to/understand-your-bills-and-charges: the billing period is in
Pacific Time for every customer), so this counts the same way. A
piece's characters are counted before it's sent (two apps at once can't both squeeze under the cap) and given back
if Google refused it. Only this server's calls are counted: other use of the same Google account isn't seen.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from paths import data_path
from tools.tts._google import TTSError

FREE_CHARS = 1_000_000          # Chirp 3 HD's free characters a month (cloud.google.com/text-to-speech/pricing)
USD_PER_MILLION = 30.0          # after that
KEEP_MONTHS = 13
CAP_REACHED = "TTS_CAP_REACHED"  # in every refusal, so the station can spot it and wait for next month
SETTING = "tts_paid_chars_month"
_lock = threading.Lock()


def _file() -> Path:
    return Path(os.environ.get("TTS_USAGE_FILE") or data_path("usage/tts_month.json"))


def _pacific(now: float):
    """(year, month) in US Pacific time."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        d = datetime.fromtimestamp(now, ZoneInfo("America/Los_Angeles"))
        return d.year, d.month
    except Exception:  # noqa: BLE001 - no time zone data: UTC-8 errs towards "not reset yet" in summer
        t = time.gmtime(now - 8 * 3600)
        return t.tm_year, t.tm_mon


def month(now: float | None = None) -> str:
    y, m = _pacific(time.time() if now is None else now)
    return f"{y:04d}-{m:02d}"


def resets(now: float | None = None) -> str:
    """The first day of next month: when Google's free allowance starts again (midnight Pacific time)."""
    y, m = _pacific(time.time() if now is None else now)
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}-01"


def paid_allowance() -> int:
    """Characters a month the owner lets go past the free ones (Settings > Text to speech); 0 = free only."""
    try:
        import runtime_settings
        return max(0, int(runtime_settings.get(SETTING)))
    except Exception:  # noqa: BLE001 - a broken settings store means free only, never paid use
        return 0


def _load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("months"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"months": {}}


def _save(data: dict) -> None:
    months = data["months"]
    for old in sorted(months)[:-KEEP_MONTHS]:
        del months[old]
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, f)


def _row(data: dict, mon: str) -> dict:
    return data["months"].setdefault(mon, {"characters": 0, "calls": 0, "clients": {}})


def _client_row(row: dict, client: str) -> dict:
    return row["clients"].setdefault(client, {"calls": 0, "characters": 0, "refused": 0})


def reserve(client: str, chars: int, now: float | None = None) -> str:
    """Count a piece before it's sent, or refuse it (TTS_CAP_REACHED) when it would take the month past the cap.
    Returns the month it was counted in, for give_back."""
    with _lock:
        data = _load()
        mon = month(now)
        row = _row(data, mon)
        limit = FREE_CHARS + paid_allowance()
        if row["characters"] + chars > limit:
            _client_row(row, client)["refused"] += 1
            _save(data)
            left = max(0, limit - row["characters"])
            raise TTSError(
                f"{CAP_REACHED}: this month has used {row['characters']:,} of its {limit:,} characters "
                f"({FREE_CHARS:,} free" + (f" + {limit - FREE_CHARS:,} paid" if limit > FREE_CHARS else ", no paid")
                + f"), so this {chars:,}-character piece is refused ({left:,} left). The count starts again on "
                f"{resets(now)} (Pacific time). Only the owner can allow paid characters: Settings > Text to speech.")
        row["characters"] += chars
        row["calls"] += 1
        c = _client_row(row, client)
        c["calls"] += 1
        c["characters"] += chars
        _save(data)
        return mon


def give_back(client: str, chars: int, mon: str) -> None:
    """Undo a reserve whose piece Google didn't speak. Never raises."""
    try:
        with _lock:
            data = _load()
            row = _row(data, mon)
            c = _client_row(row, client)
            row["characters"] = max(0, row["characters"] - chars)
            row["calls"] = max(0, row["calls"] - 1)
            c["characters"] = max(0, c["characters"] - chars)
            c["calls"] = max(0, c["calls"] - 1)
            _save(data)
    except (OSError, ValueError, TypeError):
        pass


def summary(now: float | None = None) -> dict:
    """This month's totals against the cap."""
    mon = month(now)
    row = _load()["months"].get(mon) or {"characters": 0, "calls": 0, "clients": {}}
    paid = paid_allowance()
    used = int(row["characters"])
    return {"month": mon, "characters": used, "calls": int(row["calls"]), "free_allowance": FREE_CHARS,
            "free_left": max(0, FREE_CHARS - used), "paid_allowance": paid,
            "left": max(0, FREE_CHARS + paid - used), "resets": resets(now),
            "paid_characters": max(0, used - FREE_CHARS),
            "paid_usd_estimate": round(max(0, used - FREE_CHARS) * USD_PER_MILLION / 1_000_000, 2),
            "clients": {k: dict(v) for k, v in sorted((row.get("clients") or {}).items())}}
