"""spend.today. See ../../capabilities/spend/today.md."""
from __future__ import annotations

import spend
from registry import tool


@tool(name="today", category="spend", doc="spend/today.md")
def today(client: str = "") -> dict:
    """What each app (API client) has spent today on PAID images and language-model calls, in dollars, against the
    daily cap the owner set for it on the API page. An app at its cap has its paid calls refused with a reason that
    starts SPEND_CAP_REACHED; its free and local tools keep working. Read-only.

    Args:
        client: one app's name; empty lists every app that spent today or has a cap ("owner" is the owner's own use,
            counted but never capped).

    Returns:
        {"date", "currency": "USD", "clients": [{"client", "image", "llm", "total", "cap_usd", "left_usd",
         "capped"}], "note"}
    """
    rows = spend.today_all()
    if client:
        cap = spend.cap_of(client)
        rows = [r for r in rows if r["client"] == client] or [
            {"client": client, **spend.spent(client), "cap_usd": cap, "left_usd": cap, "capped": cap == 0}]
    return {"date": spend._today(), "currency": "USD", "clients": rows,
            "note": "Estimates where a provider doesn't report its own cost; an image model with no listed price counts "
                    f"as ${spend.UNKNOWN_IMAGE_USD:.2f}. Days are UK days. Caps are set by the owner only: Control Panel "
                    "> API access > Spend cap."}
