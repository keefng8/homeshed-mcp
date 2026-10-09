"""bugs.sync: the HomeShed Pro known-bugs feed. See ../../capabilities/bugs/known.md. Needs a Pro key (mavis_pro.py)."""
from __future__ import annotations

import json

from registry import tool
from tools.bugs.known import FEED_SLUG, BugsError, import_feed


@tool(name="sync", category="bugs", doc="bugs/known.md")
def sync() -> dict:
    """Fetch the known-bugs feed from Mavis Pro with this install's Pro token and merge it in, so bugs.find knows every
    fix Pro members get. Needs a Pro key (the Pro page, or `homeshed-mcp packs`).

    Returns:
        {"added", "updated", "total"}.
    """
    import httpx

    import mavis_pro
    import vault
    url = mavis_pro.api_url()  # the Settings address, else the Pro website's backend; the Pro API is under /api
    token = vault.secret("MAVIS_PRO_TOKEN") or ""
    if not (url and token):
        raise BugsError("HomeShed Pro isn't connected here: paste your Pro key on the Pro page.")
    try:
        r = httpx.get(f"{url}/pro-api/packs/{FEED_SLUG}", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    except httpx.RequestError:
        raise BugsError("The Pro website isn't reachable right now.") from None
    if r.status_code in (401, 403):
        raise BugsError("The Pro website refused this install's key: check your membership is active, or paste a new key.")
    if r.status_code == 404:
        raise BugsError("HomeShed Pro has no known-bugs feed yet.")
    if r.status_code != 200:
        raise BugsError(f"The Pro website answered HTTP {r.status_code}.")
    try:
        content = (r.json().get("data") or {}).get("content")
        pack = json.loads(content) if isinstance(content, str) else content
    except (ValueError, AttributeError):
        raise BugsError("The known-bugs feed was unreadable.") from None
    return import_feed(pack)
