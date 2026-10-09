"""printify.product.update_live. See ../../capabilities/printify/update_live.md.

2026-10-08 (the shop's project manager and director): live listings needed hand edits in Printify. A published
product's title, description, tags and prices reach Etsy only through Printify: an edit (PUT), then a publish of just
the changed parts. That changes a public listing, so this tool only checks the change and queues it
(printify_pending.py, action "update"); the owner approves each one in the Control Panel, and execute_update runs then,
after checking the product hasn't changed since it was asked. SKUs never change on a live product: Printify matches
Etsy orders to products by SKU (R&D, 2026-10-08), so a new one can stop orders arriving.
"""
from __future__ import annotations

from registry import tool
from tools.commerce import _http as h
from tools.printify import _client as pf
from tools.printify import update as up

PUBLISH_FLAGS = ("title", "description", "images", "variants", "tags", "keyFeatures", "shipping_template")
LABEL = "Updating live Printify products"


def _live(pid: str) -> dict:
    """The published product, or why it can't be updated."""
    p = pf.get(f"/v1/shops/{pf.shop_id()}/products/{pid}.json") or {}
    if not isinstance(p, dict) or not p.get("id"):
        raise h.CommerceError("Printify has no such product in this shop")
    ext = p.get("external") if isinstance(p.get("external"), dict) else {}
    if not ext.get("id"):
        raise h.CommerceError("that product isn't published: edit the draft with printify.product.update instead")
    if p.get("is_locked"):
        raise h.CommerceError("Printify is busy with that product (it's locked); try again in a few minutes")
    return p


def _said(old: dict, title: str, tags: list[str] | None, prices: dict[int, int]) -> str:
    """The change in a few words, for the owner's approval card."""
    parts = []
    if "title" in old:
        parts.append(f'title "{h.trim(old["title"], 40)}" -> "{h.trim(title, 40)}"')
    if "description" in old:
        parts.append("description rewritten")
    if "tags" in old:
        was, now = set(old["tags"] or []), set(tags or [])
        parts.append("tags " + " ".join([f"+{t}" for t in sorted(now - was)] + [f"-{t}" for t in sorted(was - now)]))
    if "prices" in old:
        parts.append("prices " + ", ".join(f"{int(p) / 100:.2f}->{prices[int(v)] / 100:.2f}"
                                           for v, p in list(old["prices"].items())[:4]))
    return "; ".join(parts)


@tool(name="product.update_live", category="printify", doc="printify/update_live.md")
def product_update_live(product_id: str, title: str = "", description: str = "", tags: list[str] | None = None,
                        variants: list[dict] | None = None) -> dict:
    """Ask to update a PUBLISHED Printify product's title, description, tags or variant prices on the owner's Etsy
    shop. It is NOT changed by this call: it's checked, then waits for the owner's approval in the Control Panel; only
    then is it edited and re-published (just the changed parts). SKUs can't change on a live product (Printify matches
    Etsy orders by SKU). No price under the 30% margin floor. At most PRINTIFY_LIVE_UPDATE_DAILY_MAX a day (default
    10). Off until the owner switches "Let apps ask to update live Printify products" on. A draft: printify.product.update.

    Args:
        product_id: the published product's id (printify.products.list).
        title: the new title, 1-140 characters; empty keeps it.
        description: the new description, up to 5,000 characters; empty keeps it.
        tags: the new full tag list (up to 13, each up to 20 characters: Etsy's limits); omit to keep them.
        variants: [{"id": variant id, "price_pence": new price}] for the variants to reprice; the others keep theirs.

    Returns:
        {"pending": true, "id", "update", "changed", "old", "expires_in_h", "how"}: the approval request, not a change
        on the shop; {"pending": false, "changed": []} when the product is already as asked.
    """
    import printify_pending
    pid = h.check_hex_id(product_id, "product_id")
    title, description, tags, prices, _ = up.check_input(title, description, tags, variants, skus_allowed=False)
    h.require(pf.SETTING, "Printify")
    h.require(pf.LIVE_UPDATE_SETTING, LABEL)
    p = _live(pid)
    body, old = up.changes(p, title, description, tags, prices, {})
    if not body:
        return {"product_id": pid, "pending": False, "changed": [], "note": "already as asked: nothing queued"}
    fields = up.fields_of(body, old)
    name = h.trim(p.get("title"), 60) or "untitled"
    etsy_set = _etsy_details(p) is not None
    summary = h.trim(f'Update live "{name}" on your Etsy shop: {_said(old, title, tags, prices)}'
                     + ("; its Etsy details are queued again after" if etsy_set else ""), 300)
    checks = {"fields": fields, "listing": (p.get("external") or {}).get("handle") or (p.get("external") or {}).get("id"),
              "updated_last_24h": len(printify_pending.done_today("update")),
              "daily_max": printify_pending.update_daily_max(), "etsy_details_reapplied_after": etsy_set}
    payload = {"title": title, "description": description, "tags": tags,
               "prices": {str(v): c for v, c in prices.items()}, "old": old}
    try:
        out = printify_pending.add(pid, summary, checks, action="update", payload=payload)
    except printify_pending.PendingError as exc:
        raise h.CommerceError(str(exc)) from None
    return {**out, "changed": fields, "old": old}


def execute_update(product_id: str, payload: dict) -> dict:
    """The update itself, run only by printify_pending.decide on the owner's approval: checks the product is still
    live and still has the values it had when asked (else it changed meanwhile: ask again), then edits it (built from
    the product as it is now, so nothing else it has is sent back stale) and publishes only the changed parts."""
    pid = h.check_hex_id(product_id, "product_id")
    old = payload.get("old") or {}
    prices = {int(v): int(c) for v, c in (payload.get("prices") or {}).items()}
    p = _live(pid)
    stale = [f for f in ("title", "description", "tags") if f in old and p.get(f) != old[f]]
    now = {v.get("id"): v.get("price") for v in p.get("variants") or [] if isinstance(v, dict)}
    stale += [f"price of variant {v}" for v, c in (old.get("prices") or {}).items() if now.get(int(v)) != c]
    if stale:
        raise h.CommerceError(f"the product changed since this was asked ({', '.join(stale)}): decline it and ask again")
    body, _ = up.changes(p, payload.get("title") or "", payload.get("description") or "", payload.get("tags"), prices, {})
    if not body:
        return {"product_id": pid, "changed": [], "note": "already as asked: nothing sent"}
    shop = pf.shop_id()
    pf.put(f"/v1/shops/{shop}/products/{pid}.json", body, setting=pf.LIVE_UPDATE_SETTING, label=LABEL)
    flags = {f: f in body for f in PUBLISH_FLAGS}  # images, key features and the delivery template stay as they are
    pf.publish_post(f"/v1/shops/{shop}/products/{pid}/publish.json", flags, setting=pf.LIVE_UPDATE_SETTING, label=LABEL)
    lid = _etsy_details(p)
    return {"product_id": pid, "changed": up.fields_of(body, old), "old": old, "publishing": True,
            "note": "Printify is pushing the change to Etsy now; it takes a minute or two."
                    + (" The listing's Etsy details are queued again for your approval." if lid else ""),
            **({"reapply_etsy": lid} if lid else {})}


def _etsy_details(p: dict) -> str | None:
    """The product's Etsy listing id when etsy.listing.update has set details on it (a re-publish may undo them)."""
    lid = str((p.get("external") or {}).get("id") or "")
    try:
        from tools.etsy.write import applied
        return lid if lid and lid in applied() else None
    except Exception:  # noqa: BLE001 - no record readable: nothing to re-apply
        return None
