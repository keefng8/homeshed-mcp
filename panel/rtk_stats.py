"""RTK's token savings read on this PC, for the Savings page and the token strip when no helper gives them (the owner,
2026-11-03: "check the other features too, the same way"). RTK (Rust Token Killer) is a separate CLI some people use
with Claude Code; when it's on this PC's PATH, its own read-only stats commands are run here. Without it, nothing is
shown (never zeros). Command text from RTK's history scan is redacted before it leaves this module.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time

_SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|tk_[A-Za-z0-9]{10,}|nvapi-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{10,}|"
                     r"AKIA[0-9A-Z]{16}|Bearer\s+\S{8,}|\b(?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+)", re.I)
_discover_cache: dict = {"t": 0.0, "data": None}
DISCOVER_TTL_S = 600


def _why(exc: Exception) -> str:
    """A fixed reason for the page, never the exception's own text (CodeQL py/stack-trace-exposure)."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return "rtk took too long"
    if isinstance(exc, ValueError):
        return "rtk's answer wasn't JSON"
    return "rtk couldn't be run"


def rtk_bin() -> str | None:
    return shutil.which("rtk")


def _json(args: list[str], timeout: int) -> dict:
    exe = rtk_bin()
    if not exe:
        raise OSError("rtk isn't installed")
    proc = subprocess.run([exe, *args, "--format", "json"], capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return json.loads(proc.stdout)


def summary() -> dict:
    try:
        return {"available": True, **(_json(["gain"], 10).get("summary") or {})}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"available": False, "error": _why(exc)}


def daily() -> dict:
    try:
        d = _json(["gain", "--daily"], 15)
        return {"available": True, "summary": d.get("summary", {}), "daily": d.get("daily", [])}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"available": False, "error": _why(exc)}


def discover(refresh: bool = False) -> dict:
    now = time.time()
    if not refresh and _discover_cache["data"] and now - _discover_cache["t"] < DISCOVER_TTL_S:
        return {**_discover_cache["data"], "cached_s": int(now - _discover_cache["t"])}
    try:
        d = _json(["discover", "--all", "--since", "30", "--limit", "15"], 90)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"available": False, "error": _why(exc)}

    def clean(v):
        return _SECRET.sub("[redacted]", str(v or ""))[:120]
    data = {
        "available": True,
        "sessions_scanned": d.get("sessions_scanned"), "total_commands": d.get("total_commands"),
        "already_rtk": d.get("already_rtk"), "since_days": d.get("since_days"),
        "missed": [{"command": clean(m.get("command")), "count": m.get("count"),
                    "rtk_equivalent": clean(m.get("rtk_equivalent")), "category": clean(m.get("category")),
                    "estimated_savings_tokens": m.get("estimated_savings_tokens"),
                    "estimated_savings_pct": m.get("estimated_savings_pct"), "status": clean(m.get("rtk_status"))}
                   for m in d.get("supported", [])],
        # base command names only: the "example" lines can hold arguments and tokens
        "unsupported_top": [{"command": clean(u.get("base_command")), "count": u.get("count")}
                            for u in sorted(d.get("unsupported", []), key=lambda u: -(u.get("count") or 0))[:10]],
        "unsupported_count": len(d.get("unsupported", [])),
    }
    _discover_cache.update(t=now, data=data)
    return {**data, "cached_s": 0}
