"""Is this install on HomeShed Pro? Pro features ask here. The answer comes from the Pro connection (mavis_pro.py, which
asks the Pro website at most every 10 minutes, and only once a Pro key has been pasted). Without a connection, or with a
lapsed membership, the install is on the free plan.

An unreachable Pro website isn't an answer: a membership confirmed within the last 7 days holds. Only the site saying no
(a lapsed membership, a revoked key) ends Pro at once. The confirmed time is kept on disk, so a restart keeps the grace
(a redeploy while the Pro website answered 502 once dropped a member's install to free, 2026-09-30).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from paths import data_path

GRACE_S = 7 * 86400
STATE = Path(os.environ.get("PRO_STATE_FILE") or data_path("usage/pro.json"))
WRITE_EVERY_S = 600  # check() runs on every Pro call; the disk hears about it at most every 10 minutes
_confirmed_at: float | None = None  # None: not read from STATE yet
_written_at = 0.0


def _last_confirmed() -> float:
    global _confirmed_at
    if _confirmed_at is None:
        try:
            _confirmed_at = float(json.loads(STATE.read_text(encoding="utf-8"))["confirmed_at"])
        except (OSError, ValueError, TypeError, KeyError):
            _confirmed_at = 0.0
    return _confirmed_at


def _confirm(now: float) -> None:
    global _confirmed_at, _written_at
    _confirmed_at = now
    if now - _written_at < WRITE_EVERY_S:
        return
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"confirmed_at": now}), encoding="utf-8")
        os.replace(tmp, STATE)
        _written_at = now
    except OSError:
        pass  # the grace then lasts only while this server runs, as it did before


def check() -> tuple[bool, str]:
    """(on Pro, why not: empty, or a sentence for the owner). Never raises."""
    try:
        import mavis_pro
    except ImportError:  # a build without the Pro module: free
        return False, ""
    try:
        st = mavis_pro.status()
    except Exception:  # noqa: BLE001 - treated like an unreachable site, never a failed tool call
        st = {"connected": True, "unreachable": True, "reason": "Couldn't check the Pro membership just now."}
    now = time.time()
    if st.get("pro"):
        _confirm(now)
        return True, ""
    # 0 <=: a confirmed time in the future is an edited file, not a membership (to-do #19 bypass test, 2026-10-01)
    if st.get("unreachable") and 0 <= now - _last_confirmed() < GRACE_S:
        return True, ""
    return False, st.get("reason") or ""
