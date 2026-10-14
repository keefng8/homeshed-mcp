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


def _last_confirmed(token: str) -> float:
    """When the newest signed Pro statement kept on disk was signed, or 0. A time typed into the file counts for
    nothing: only the Pro site's signature does (to-do #19 round 8)."""
    global _confirmed_at
    if _confirmed_at is None:
        try:
            proof = json.loads(STATE.read_text(encoding="utf-8")).get("proof")
        except (OSError, ValueError, TypeError, AttributeError):
            proof = None
        import pro_proof
        _confirmed_at = float(pro_proof.signed_at(proof, token) or 0) if proof else 0.0
    return _confirmed_at


def _confirm(proof: str | None, token: str) -> None:
    global _confirmed_at, _written_at
    import pro_proof
    iat = pro_proof.signed_at(proof, token) if proof else None
    if not iat:
        return
    _confirmed_at = float(iat)
    if time.time() - _written_at < WRITE_EVERY_S:
        return
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"proof": proof}), encoding="utf-8")
        os.replace(tmp, STATE)
        _written_at = time.time()
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
    token = mavis_pro.vault.secret(mavis_pro.TOKEN) or ""
    if st.get("pro"):  # status() counts only a statement signed with the Pro site's key
        _confirm(mavis_pro.last_proof(), token)
        return True, ""
    # 0 <=: a signed time in the future isn't a membership either (to-do #19 bypass test, 2026-10-01)
    if st.get("unreachable") and token and 0 <= now - _last_confirmed(token) < GRACE_S:
        return True, ""
    return False, st.get("reason") or ""
