"""printify.product.publish (to-do #59). See ../../capabilities/printify/publish.md.

Publishing puts a product on the owner's Etsy shop (through Printify's Etsy connection), where buyers see it and
Etsy charges its listing fee. So the tool only checks the draft and queues the publish (printify_pending.py); the
owner approves each one in the Control Panel, and execute() runs then, after checking the draft again.
"""
from __future__ import annotations

from registry import tool
from tools.commerce import _http as h
from tools.printify import _client as pf

PUBLISH_FIELDS = ("title", "description", "images", "variants", "tags", "keyFeatures", "shipping_template")


def _draft(product_id: str) -> tuple[str, dict]:
    """(checked id, the product) for a draft that can be published: it exists, isn't published and isn't locked."""
    pid = h.check_hex_id(product_id, "product_id")
    p = pf.get(f"/v1/shops/{pf.shop_id()}/products/{pid}.json") or {}
    if not p.get("id"):
        raise h.CommerceError(f"no Printify product {pid}")
    ext = p.get("external") if isinstance(p.get("external"), dict) else {}
    if ext.get("id"):
        raise h.CommerceError("that product is already published (it has a shop listing)")
    if p.get("is_locked"):
        raise h.CommerceError("Printify is busy with that product (it's locked); try again in a few minutes")
    on = [v for v in (p.get("variants") or []) if isinstance(v, dict) and v.get("is_enabled")]
    if not on:
        raise h.CommerceError("that product has no enabled variants to sell")
    return pid, p


def station_pause() -> str | None:
    """Why publishing is paused, or None: an owner's private add-on (private_publish_guard.py) may pause it, e.g. while
    their own shop app's stop-loss is on (2026-10-08). Without one, nothing pauses it."""
    try:
        import private_publish_guard
    except ImportError:
        return None
    try:
        return private_publish_guard.paused()
    except Exception:  # noqa: BLE001 - a broken add-on pauses nothing: the owner's approval is the gate
        return None


@tool(name="product.publish", category="printify", doc="printify/publish.md")
def product_publish(product_id: str) -> dict:
    """Ask to publish a Printify draft to the owner's connected shop (Etsy). It is NOT published by this call: it's
    checked, then waits for the owner's approval in the Control Panel; only then does it go live, where buyers see it
    and Etsy charges its listing fee. At most PRINTIFY_PUBLISH_DAILY_MAX a day (default 5). Off until the owner
    switches "Let apps ask to publish Printify products" on.

    Args:
        product_id: the draft's id (printify.product.create returned it; printify.products.list shows them).

    Returns:
        {"pending": true, "id", "publish", "expires_in_h", "how"}: the approval request, not a published listing.
    """
    import printify_pending
    h.require(pf.SETTING, "Printify")
    h.require(pf.PUBLISH_SETTING, "Publishing Printify products")
    if paused := station_pause():
        raise h.CommerceError(paused)
    pid, p = _draft(product_id)
    on = [v for v in p.get("variants") or [] if isinstance(v, dict) and v.get("is_enabled")]
    prices = sorted(v["price"] / 100 for v in on if isinstance(v.get("price"), (int, float)))
    price = (f"{prices[0]:.2f}" if prices and prices[0] == prices[-1] else
             f"{prices[0]:.2f}-{prices[-1]:.2f}" if prices else "no price")
    title = h.trim(p.get("title"), 100) or "untitled"
    summary = f'Publish "{title}" ({len(on)} variants, {price}) to your Etsy shop: it goes live, Etsy fee applies'
    checks = {"title": title, "variants_enabled": len(on), "price": price, "images": len(p.get("images") or []),
              "tags": len(p.get("tags") or []), "published_last_24h": len(printify_pending.published_today()),
              "daily_max": printify_pending.daily_max()}
    try:
        return printify_pending.add(pid, summary, checks)
    except printify_pending.PendingError as exc:
        raise h.CommerceError(str(exc)) from None


def execute(product_id: str) -> dict:
    """The publish itself, run only by printify_pending.decide on the owner's approval: checks the draft again (it
    may have changed or been published by hand since), then asks Printify to publish everything."""
    pid, p = _draft(product_id)
    pf.publish_post(f"/v1/shops/{pf.shop_id()}/products/{pid}/publish.json", {f: True for f in PUBLISH_FIELDS})
    return {"product_id": pid, "title": h.trim(p.get("title"), 100), "publishing": True,
            "note": "Printify is publishing it to Etsy now; it takes a minute or two. etsy.listings.list shows it once "
                    "it's live, and printify.product.get then has its external id."}
