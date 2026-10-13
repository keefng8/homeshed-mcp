"""etsy.shop.get, etsy.listings.list, etsy.listing.get, etsy.shipping_destinations, etsy.orders.list,
etsy.order.get (read-only).
See ../../capabilities/etsy/read.md. Results are compact (under about 5,000 characters) and leave out buyers'
personal details: names, emails, addresses and messages."""
from __future__ import annotations

from registry import tool
from tools.commerce import _http as h
from tools.etsy import _client as et

LISTING_STATES = ("active", "inactive", "sold_out", "draft", "expired")


def _ts(v):
    return int(v) if isinstance(v, (int, float)) else None


@tool(name="shop.get", category="etsy", doc="etsy/read.md")
def shop_get() -> dict:
    """The configured Etsy shop (ETSY_SHOP_ID): name, title, currency, counts, reviews, vacation mode, and its
    production partners (what a listing's production_partner_ids should name). Read-only; needs the owner's
    commerce_etsy_enabled switch and the ETSY_* entries.

    Returns:
        {"shop_id", "shop_name", "title", "currency", "active_listings", "sold", "review_average", "review_count",
         "is_vacation", "url", "created", "production_partners": [{"id", "name", "location"}] or null when the shop's
         connection can't read them, "return_policies": [{"id", "accepts_returns", "accepts_exchanges",
         "return_deadline_days"}] or null likewise}
    """
    sid = et.shop_id()
    s = et.get(f"/v3/application/shops/{sid}", oauth=False) or {}
    try:  # the shop's own connection (shops_r); the rest of shop.get works without it
        rows = (et.get(f"/v3/application/shops/{sid}/production-partners") or {}).get("results")
        partners = [{"id": p.get("production_partner_id"), "name": h.trim(p.get("partner_name"), 60),
                     "location": h.trim(p.get("location"), 60)} for p in rows or [] if isinstance(p, dict)][:20]
    except h.CommerceError:
        partners = None
    try:  # getShopReturnPolicies: what a listing's return_policy_id names (2026-10-10, the 14-day returns check)
        rows = (et.get(f"/v3/application/shops/{sid}/policies/return") or {}).get("results")
        returns = [{"id": r.get("return_policy_id"), "accepts_returns": r.get("accepts_returns"),
                    "accepts_exchanges": r.get("accepts_exchanges"), "return_deadline_days": r.get("return_deadline")}
                   for r in rows or [] if isinstance(r, dict)][:20]
    except h.CommerceError:
        returns = None
    return et.note({"shop_id": s.get("shop_id"), "shop_name": s.get("shop_name"), "title": h.trim(s.get("title"), 120),
                    "return_policies": returns,
                    "currency": s.get("currency_code"), "active_listings": s.get("listing_active_count"),
                    "sold": s.get("transaction_sold_count"), "review_average": s.get("review_average"),
                    "review_count": s.get("review_count"), "is_vacation": s.get("is_vacation"),
                    "url": s.get("url"), "created": _ts(s.get("create_date") or s.get("created_timestamp")),
                    "production_partners": partners})


@tool(name="listings.list", category="etsy", doc="etsy/read.md")
def listings_list(state: str = "active", limit: int = 20, offset: int = 0) -> dict:
    """The shop's listings, newest first, compact: id, title, state, price, quantity, updated.

    Args:
        state: active (default), inactive, sold_out, draft or expired.
        limit: 1-20 per page.
        offset: skip this many (paging: use "next_offset").

    Returns:
        {"total", "offset", "next_offset", "listings": [{"listing_id", "title", "state", "price": {amount,
         currency}, "quantity", "updated"}]}
    """
    if state not in LISTING_STATES:
        raise h.CommerceError(f"state must be one of {', '.join(LISTING_STATES)}")
    limit, offset = h.clamp(limit, 1, 20, "limit"), h.clamp(offset, 0, 100_000, "offset")
    d = et.get(f"/v3/application/shops/{et.shop_id()}/listings",
               params={"state": state, "limit": limit, "offset": offset, "sort_on": "updated",
                       "sort_order": "desc"}) or {}
    rows = [{"listing_id": x.get("listing_id"), "title": h.trim(x.get("title"), 60), "state": x.get("state"),
             "price": h.money(x.get("price")), "quantity": x.get("quantity"),
             "updated": _ts(x.get("updated_timestamp") or x.get("last_modified_timestamp"))}
            for x in (d.get("results") or [])[:limit]]
    total = d.get("count")
    nxt = offset + len(rows) if isinstance(total, int) and offset + len(rows) < total else None
    return et.note({"total": total, "offset": offset, "next_offset": nxt, "listings": rows})


# EU countries (Etsy's "eu" region covers them too): a listing that delivers to any of these needs the EU product-safety
# (GPSR) details, so a caller checking a UK-only launch can flag it.
EU_COUNTRIES = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split())


@tool(name="listing.get", category="etsy", doc="etsy/read.md")
def listing_get(listing_id: str, full: bool = False) -> dict:
    """One listing in detail: title, description (first 1,500 characters, or 5,000 with full=true), price, quantity,
    tags, materials, taxonomy, who/when made, digital or not, its photos (count and ranks), production partners, views,
    favourites, timestamps, url.

    Args:
        listing_id: the listing's number (etsy.listings.list shows them).
        full: the description up to 5,000 characters, e.g. to check a disclosure at its end, and the listing's
            properties (attributes) with Etsy's exact names, property ids and value ids.
    """
    lid = h.check_id(listing_id, "listing_id")
    x = et.get(f"/v3/application/listings/{lid}", params={"includes": "Images"}, oauth=False) or {}
    desc = x.get("description") if isinstance(x.get("description"), str) else ""
    imgs = [i for i in (x.get("images") or []) if isinstance(i, dict)]
    return et.note({
        "listing_id": x.get("listing_id"), "shop_id": x.get("shop_id"), "title": h.trim(x.get("title"), 140),
        "description": h.trim(desc, 5000 if full else 1500), "description_chars": len(desc), "state": x.get("state"),
        "price": h.money(x.get("price")),
        "quantity": x.get("quantity"), "tags": (x.get("tags") or [])[:13], "materials": (x.get("materials") or [])[:13],
        "taxonomy_id": x.get("taxonomy_id"), "who_made": x.get("who_made"), "when_made": x.get("when_made"),
        "is_digital": x.get("is_digital"), "is_customizable": x.get("is_customizable"),
        "shipping_profile_id": x.get("shipping_profile_id"), "return_policy_id": x.get("return_policy_id"),
        "shop_section_id": x.get("shop_section_id"),
        "images": {"count": len(imgs), "ranks": sorted(i.get("rank") for i in imgs if isinstance(i.get("rank"), int))},
        "production_partner_ids": et.partner_ids(x),
        "production_partners": [{"id": p.get("production_partner_id"), "name": h.trim(p.get("partner_name"), 60)}
                                for p in x.get("production_partners") or [] if isinstance(p, dict)][:10],
        "views": x.get("views"), "favorites": x.get("num_favorers"), "created": _ts(x.get("created_timestamp")),
        "updated": _ts(x.get("updated_timestamp")), "url": x.get("url"),
        **({"properties": _properties(x.get("shop_id"), lid)} if full else {})})


def _properties(shop_id, lid) -> list[dict] | str:
    """The listing's attributes with Etsy's exact names and ids (2026-10-13: a wrong string refuses a whole update)."""
    try:
        res = (et.get(f"/v3/application/shops/{int(shop_id)}/listings/{lid}/properties", oauth=False) or {}).get("results")
    except (h.CommerceError, TypeError, ValueError) as exc:
        return f"not read: {h.trim(str(exc), 120)}"
    return [{"property_id": p.get("property_id"), "name": p.get("property_name"), "value_ids": p.get("value_ids"),
             "values": p.get("values")} for p in res or [] if isinstance(p, dict)][:30]


@tool(name="shipping_destinations", category="etsy", doc="etsy/read.md")
def shipping_destinations(shipping_profile_id: str) -> dict:
    """Where one of the shop's delivery profiles delivers: each destination's country (or Etsy region), its prices and
    delivery days, and includes_eu (any EU country, or Etsy's "eu" region). Needs the shop's Etsy connection.

    Args:
        shipping_profile_id: from etsy.listing.get's shipping_profile_id.

    Returns:
        {"shipping_profile_id", "count", "includes_eu", "destinations": [{"country", "region", "primary_cost",
        "secondary_cost", "min_days", "max_days"}]}
    """
    spid = h.check_id(shipping_profile_id, "shipping_profile_id")
    d = et.get(f"/v3/application/shops/{et.shop_id()}/shipping-profiles/{spid}/destinations",
               params={"limit": 100}) or {}
    rows = [{"country": r.get("destination_country_iso"), "region": r.get("destination_region"),
             "primary_cost": h.money(r.get("primary_cost")), "secondary_cost": h.money(r.get("secondary_cost")),
             "min_days": r.get("min_delivery_days"), "max_days": r.get("max_delivery_days")}
            for r in (d.get("results") or []) if isinstance(r, dict)]
    eu = any(r["region"] == "eu" or (r["country"] or "").upper() in EU_COUNTRIES for r in rows)
    return et.note({"shipping_profile_id": int(spid), "count": d.get("count", len(rows)), "includes_eu": eu,
                    "destinations": rows[:100]})


def _receipt_summary(r: dict) -> dict:
    tx = r.get("transactions") or []
    return {"receipt_id": r.get("receipt_id"), "status": r.get("status"), "created": _ts(r.get("created_timestamp")),
            "is_paid": r.get("is_paid"), "is_shipped": r.get("is_shipped"), "total": h.money(r.get("grandtotal")),
            "item_count": sum(int(t.get("quantity") or 0) for t in tx if isinstance(t, dict)) or len(tx),
            "country": r.get("country_iso")}


@tool(name="orders.list", category="etsy", doc="etsy/read.md")
def orders_list(limit: int = 20, offset: int = 0, was_paid: str = "any", was_shipped: str = "any") -> dict:
    """The shop's orders (Etsy "receipts"), newest first, compact. No buyer names, emails or addresses.

    Args:
        limit: 1-20 per page.
        offset: skip this many (paging: use "next_offset").
        was_paid / was_shipped: "any" (default), "yes" or "no".

    Returns:
        {"total", "offset", "next_offset", "orders": [{"receipt_id", "status", "created", "is_paid", "is_shipped",
         "total": {amount, currency}, "item_count", "country"}]}
    """
    limit, offset = h.clamp(limit, 1, 20, "limit"), h.clamp(offset, 0, 100_000, "offset")
    params: dict = {"limit": limit, "offset": offset, "sort_on": "created", "sort_order": "desc"}
    for name, value in (("was_paid", was_paid), ("was_shipped", was_shipped)):
        if value not in ("any", "yes", "no"):
            raise h.CommerceError(f"{name} must be any, yes or no")
        if value != "any":
            params[name] = "true" if value == "yes" else "false"
    d = et.get(f"/v3/application/shops/{et.shop_id()}/receipts", params=params) or {}
    rows = [_receipt_summary(r) for r in (d.get("results") or [])[:limit] if isinstance(r, dict)]
    total = d.get("count")
    nxt = offset + len(rows) if isinstance(total, int) and offset + len(rows) < total else None
    return et.note({"total": total, "offset": offset, "next_offset": nxt, "orders": rows})


@tool(name="order.get", category="etsy", doc="etsy/read.md")
def order_get(receipt_id: str) -> dict:
    """One order (receipt): status, money, its items and shipments. No buyer names, emails, addresses or messages.

    Args:
        receipt_id: the order's number (etsy.orders.list shows them).
    """
    rid = h.check_id(receipt_id, "receipt_id")
    r = et.get(f"/v3/application/shops/{et.shop_id()}/receipts/{rid}") or {}
    items = [{"listing_id": t.get("listing_id"), "title": h.trim(t.get("title"), 80), "quantity": t.get("quantity"),
              "price": h.money(t.get("price")), "sku": t.get("sku"), "is_digital": t.get("is_digital")}
             for t in (r.get("transactions") or [])[:20] if isinstance(t, dict)]
    ships = [{"carrier": s.get("carrier_name"), "tracking_code": s.get("tracking_code"),
              "shipped": _ts(s.get("shipment_notification_timestamp"))}
             for s in (r.get("shipments") or [])[:5] if isinstance(s, dict)]
    return et.note({**_receipt_summary(r), "subtotal": h.money(r.get("subtotal")),
                    "shipping": h.money(r.get("total_shipping_cost")), "tax": h.money(r.get("total_tax_cost")),
                    "discount": h.money(r.get("discount_amt")), "items": items, "shipments": ships,
                    "updated": _ts(r.get("updated_timestamp"))})
