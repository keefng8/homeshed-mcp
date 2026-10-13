"""spend.cap_set. See ../../capabilities/spend/cap_set.md."""
from __future__ import annotations

import clients
import spend
from registry import tool

MAX_DELEGATED_USD = 5.0  # a higher cap is the owner's own call, on the Control Panel


class SpendCapError(ValueError):
    pass


@tool(name="cap_set", category="spend", doc="spend/cap_set.md")
def cap_set(client: str, usd: float) -> dict:
    """Set an app's daily cap on paid images and language-model calls, in dollars, from 0 to 5. Only the owner's own
    AI sessions can, and only for apps the owner opened to it (clients.set_cap_delegated); any other app keeps the cap
    the owner set. 0 means local and free tools only. Logged in the API access audit.

    Args:
        client: the app's name, as spend.today lists it.
        usd: dollars a day, 0-5.

    Returns:
        {"client", "cap_usd", "spent_today", "left_usd"}
    """
    caller = clients.current_client.get()
    if caller and caller != "owner":
        raise SpendCapError("only the owner's own sessions can set spend caps")
    name = str(client or "").strip()
    if not clients.cap_delegated(name):
        raise SpendCapError(f"{name or 'that app'} isn't open to cap changes by AI sessions; its cap is the owner's "
                            "to set (Control Panel > API access > Spend cap)")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not 0 <= float(usd) <= MAX_DELEGATED_USD:
        raise SpendCapError(f"usd must be from 0 to {MAX_DELEGATED_USD:g}; a higher cap is the owner's own call")
    clients.set_spend_cap(name, float(usd))
    spent = spend.spent(name)
    cap = spend.cap_of(name)
    return {"client": name, "cap_usd": cap, "spent_today": spent,
            "left_usd": None if cap is None else round(max(0.0, cap - spent["total"]), 6)}
