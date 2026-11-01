"""printify.product.delete_draft. See ../../capabilities/printify/delete_draft.md.

The owner, 2026-11-01: "Allow for director to delete too" (a "TEST ... do not publish" draft the PM made had no tool
to remove it). A bot the owner names deletes one UNPUBLISHED draft: never a product on a sales channel, one mid-publish,
one waiting for publish approval or one published today. The whole draft is kept in usage/printify_deleted.json first,
so it can be made again, and each delete is audited with its reason.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from paths import data_path
from registry import tool
from tools.commerce import _http as h
from tools.printify import _client as pf
from tools.printify import update as up

BOTS_SETTING = "printify_draft_delete_bots"
DELETED_FILE = Path(os.environ.get("PRINTIFY_DELETED_FILE") or data_path("usage/printify_deleted.json"))
KEPT = 50


def _keep(product: dict, bot: str, reason: str) -> None:
    try:
        rows = json.loads(DELETED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    rows = ([{"at": time.time(), "by": f"bot:{bot}", "reason": reason, "product": product}] + list(rows))[:KEPT]
    DELETED_FILE.parent.mkdir(parents=True, exist_ok=True)
    DELETED_FILE.write_text(json.dumps(rows), encoding="utf-8")


@tool(name="product.delete_draft", category="printify", doc="printify/delete_draft.md")
def product_delete_draft(product_id: str, bot: str, reason: str) -> dict:
    """Delete one UNPUBLISHED Printify draft (a test or a duplicate), by a bot the owner named in Settings > Shops and
    social > "Bots that may delete Printify drafts". A published product, one mid-publish, one waiting for publish
    approval or one published today is refused. The draft is saved first and the delete audited with its reason.

    Args:
        product_id: the draft's id (printify.products.list).
        bot: the deleting bot's name, e.g. "director".
        reason: why it goes, 3-200 characters (e.g. "test draft for the zip back placement").

    Returns:
        {"product_id", "deleted": true, "title", "kept_in": "usage/printify_deleted.json"}.
    """
    import clients
    import printify_pending as pp
    pid = h.check_hex_id(product_id, "product_id")
    bot = str(bot or "").strip().lower()
    reason = " ".join(str(reason or "").split())
    if not pp.BOT_NAME_RE.match(bot):
        raise h.CommerceError("bot must be a bot's name (a-z, 0-9, _ or -, up to 30)")
    if not 3 <= len(reason) <= 200:
        raise h.CommerceError("say why it's deleted (3-200 characters)")
    if clients.current_client.get() is not None:
        raise h.CommerceError("only the owner's own sessions may delete drafts, not an app")
    if bot not in {b.lower() for b in pp.trusted_clients(BOTS_SETTING)}:
        raise h.CommerceError(f'"{bot}" isn\'t a bot the owner lets delete drafts: the owner can name it in Settings > '
                              'Shops and social > "Bots that may delete Printify drafts"')
    path = f"/v1/shops/{pf.shop_id()}/products/{pid}.json"
    product = pf.get(path) or {}
    if not isinstance(product, dict) or not product.get("id"):
        raise h.CommerceError("Printify has no such product in this shop")
    if why := up._not_a_draft(pid, product):
        raise h.CommerceError(f"only unpublished drafts can be deleted, and this one isn't: {why}")
    _keep(product, bot, reason)
    pf.delete(path)
    clients._audit("printify draft deleted", None, product=pid, by=f"bot:{bot}", reason=reason[:200])
    return {"product_id": pid, "deleted": True, "title": h.trim(product.get("title"), 80),
            "kept_in": "usage/printify_deleted.json"}
