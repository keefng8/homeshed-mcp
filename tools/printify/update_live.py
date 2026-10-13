"""printify.product.update_live. See ../../capabilities/printify/update_live.md.

2026-10-08 (the shop's project manager and director): live listings needed hand edits in Printify. A published
product's title, description, tags and prices reach Etsy only through Printify: an edit (PUT), then a publish of just
the changed parts. That changes a public listing, so this tool only checks the change and queues it
(printify_pending.py, action "update"); the owner approves each one in the Control Panel, and execute_update runs then,
after checking the product hasn't changed since it was asked. SKUs never change on a live product: Printify matches
Etsy orders to products by SKU (R&D, 2026-10-08), so a new one can stop orders arriving.
"""
from __future__ import annotations

import threading
import time

from registry import tool
from tools.commerce import _http as h
from tools.printify import _client as pf
from tools.printify import update as up

PUBLISH_FLAGS = ("title", "description", "images", "variants", "tags", "keyFeatures", "shipping_template")
# What a publish re-sends that can undo a listing's Etsy details (the director, 2026-10-09): a words-only publish left
# them alone (category, materials, partners, delivery profile and attributes all intact on 5 listings, checked on Etsy;
# the shop app also re-checks each listing after a words change), so only these queue them again. A price change
# re-sends the variants (Etsy's inventory): kept, to be safe.
RESETS_ETSY_DETAILS = ("variants", "images", "shipping_template")
LABEL = "Updating live Printify products"
# The re-activation guard (the owner's decision, 2026-10-10): on 10 Oct an approved update
# re-published the Black Staffy tee and Printify switched its Etsy listing back on after it had been turned off. So an
# update of a listing that isn't active on Etsy is held, when asked and again on approval, for every client; and each
# re-publish is checked again on Etsy a few minutes later (Printify publishes in the background).
OFF_ON_ETSY = "listing is off on Etsy: the owner turns it on first"
RECHECK_AT_S = (20, 60, 120, 180)  # while Printify publishes (a minute or two), then once it's done


def _etsy_state(p: dict) -> str:
    """The Etsy listing's state ("active", "inactive", "draft", ...), read signed in (an inactive listing isn't public)."""
    from tools.etsy import _client as et
    lid = str((p.get("external") or {}).get("id") or "")
    x = et.get(f"/v3/application/listings/{lid}") or {}
    return str(x.get("state") or "") if isinstance(x, dict) else ""


def _hold_unless_active(p: dict) -> None:
    try:
        state = _etsy_state(p)
    except h.CommerceError as exc:  # can't tell: hold, never guess (the guard's point is a listing that's off)
        raise h.CommerceError(f"couldn't check on Etsy whether the listing is on ({exc}); it stays held: try again "
                              "once Etsy answers") from None
    if state != "active":
        raise h.CommerceError(f"{OFF_ON_ETSY} (Etsy says {state or 'nothing'})")


def _recheck_later(p: dict, before: str, sleep=time.sleep) -> None:
    """The backstop: the listing was on when the publish started (the hold sees to that), but someone may turn it off
    while Printify is still publishing, and Printify's publish then turns it back on (10 Oct). Etsy is read a few times
    over three minutes; off at any point and on at the end gets the owner a high-priority push."""
    def run():
        seen, waited = [before], 0
        for at in RECHECK_AT_S:
            sleep(at - waited)
            waited = at
            try:
                seen.append(_etsy_state(p))
            except h.CommerceError:
                continue
        after = seen[-1]
        if after == "active" and any(s != "active" for s in seen):
            was = next(s for s in seen if s != "active") or "off"
            from tools.notify.send import send
            title = h.trim(p.get("title"), 80) or "a listing"
            send(f'"{title}" (Etsy {(p.get("external") or {}).get("id")}) was {was} and is ON again after a '
                 "Printify re-publish the tool server ran. Turn it off on Etsy if it should stay off.",
                 title="Etsy listing switched back on", priority="high", tags=["warning"])
    threading.Thread(target=run, name="etsy-reactivation-check", daemon=True).start()


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


def _said(old: dict, title: str, tags: list[str] | None, prices: dict[int, int],
          enabled: dict[int, bool] | None = None) -> str:
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
    if "enabled" in old:
        off = [v for v in old["enabled"] if not (enabled or {}).get(int(v), True)]
        on = [v for v in old["enabled"] if (enabled or {}).get(int(v), False)]
        parts.append(" ".join(([f"variants off: {', '.join(off[:6])}"] if off else [])
                              + ([f"variants on: {', '.join(on[:6])}"] if on else [])))
    return "; ".join(parts)


@tool(name="product.update_live", category="printify", doc="printify/update_live.md")
def product_update_live(product_id: str, title: str = "", description: str = "", tags: list[str] | None = None,
                        variants: list[dict] | None = None, owner_approval: dict | None = None) -> dict:
    """Ask to update a PUBLISHED Printify product's title, description, tags or variant prices on the owner's Etsy
    shop. It is NOT changed by this call: it's checked, then waits for the owner's approval in the Control Panel; only
    then is it edited and re-published (just the changed parts). SKUs can't change on a live product (Printify matches
    Etsy orders by SKU). No price under the margin floor (20%, hoodies 15%). At most PRINTIFY_LIVE_UPDATE_DAILY_MAX a day (default
    10). Off until the owner switches "Let apps ask to update live Printify products" on. A draft: printify.product.update.

    Args:
        product_id: the published product's id (printify.products.list).
        title: the new title, 1-140 characters; empty keeps it.
        description: the new description, up to 5,000 characters; empty keeps it.
        tags: the new full tag list (up to 13, each up to 20 characters: Etsy's limits); omit to keep them.
        variants: [{"id": variant id, "price_pence": new price, "enabled": true/false}] for the variants to reprice
            or to put on / take off sale (never all of them off); the others keep theirs.
        owner_approval: {"id", "by", "at"}: the owner's own approval inside a trusted app (as printify.product.publish);
            then it runs at once.

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
    _hold_unless_active(p)
    enabled = up.enabled_of(variants)
    body, old = up.changes(p, title, description, tags, prices, {}, enabled)
    if not body:
        return {"product_id": pid, "pending": False, "changed": [], "note": "already as asked: nothing queued"}
    fields = up.fields_of(body, old)
    name = h.trim(p.get("title"), 60) or "untitled"
    etsy_set = any(f in body for f in RESETS_ETSY_DETAILS) and _etsy_details(p) is not None
    summary = h.trim(f'Update live "{name}" on your Etsy shop: {_said(old, title, tags, prices, enabled)}'
                     + ("; its Etsy details are queued again after" if etsy_set else ""), 300)
    checks = {"fields": fields, "listing": (p.get("external") or {}).get("handle") or (p.get("external") or {}).get("id"),
              "updated_last_24h": len(printify_pending.done_today("update")),
              "daily_max": printify_pending.update_daily_max(), "etsy_details_reapplied_after": etsy_set}
    payload = {"title": title, "description": description, "tags": tags,
               "prices": {str(v): c for v, c in prices.items()},
               "enabled": {str(v): on for v, on in enabled.items()}, "old": old}
    try:
        out = printify_pending.add(pid, summary, checks, action="update", payload=payload, owner_approval=owner_approval)
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
    on_now = {v.get("id"): bool(v.get("is_enabled", True)) for v in p.get("variants") or [] if isinstance(v, dict)}
    stale += [f"variant {v} on/off" for v, was in (old.get("enabled") or {}).items() if on_now.get(int(v)) != was]
    enabled = {int(v): bool(on) for v, on in (payload.get("enabled") or {}).items()}
    if stale:
        raise h.CommerceError(f"the product changed since this was asked ({', '.join(stale)}): decline it and ask again")
    body, _ = up.changes(p, payload.get("title") or "", payload.get("description") or "", payload.get("tags"), prices, {},
                         enabled)
    if not body:
        return {"product_id": pid, "changed": [], "note": "already as asked: nothing sent"}
    _hold_unless_active(p)  # again on approval: it may have been turned off since it was asked
    shop = pf.shop_id()
    pf.put(f"/v1/shops/{shop}/products/{pid}.json", body, setting=pf.LIVE_UPDATE_SETTING, label=LABEL)
    flags = {f: f in body for f in PUBLISH_FLAGS}  # images, key features and the delivery template stay as they are
    before = _etsy_state(p)  # read again right before the publish: the backstop below compares with it
    if before != "active":  # turned off in the seconds since: the edit is saved in Printify, but not re-published
        raise h.CommerceError(f"{OFF_ON_ETSY} (it was turned off just now; the edit is saved in Printify but not "
                              "published)")
    pf.publish_post(f"/v1/shops/{shop}/products/{pid}/publish.json", flags, setting=pf.LIVE_UPDATE_SETTING, label=LABEL)
    _recheck_later(p, before)
    lid = _etsy_details(p) if any(flags[f] for f in RESETS_ETSY_DETAILS) else None
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
