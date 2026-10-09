"""printify.shops.list, printify.products.list, printify.product.get, printify.blueprints.list,
printify.print_providers.list, printify.print_provider.get, printify.blueprint.get, printify.orders.list,
printify.order.get (read-only). See ../../capabilities/printify/read.md. Results are compact (under about 5,000
characters); delivery addresses and customer details are left out."""
from __future__ import annotations

import html
import re

from registry import tool
from tools.commerce import _http as h
from tools.printify import _client as pf


def _page(d: dict) -> dict:
    return {"page": d.get("current_page"), "last_page": d.get("last_page"), "total": d.get("total")}


def _cents(v):
    return round(v / 100, 2) if isinstance(v, (int, float)) else None


@tool(name="shops.list", category="printify", doc="printify/read.md")
def shops_list() -> dict:
    """The Printify shops this token can see: id, title, sales channel (e.g. etsy). Read-only.

    Returns:
        {"shops": [{"id", "title", "sales_channel"}], "configured_shop_id"}
    """
    d = pf.get("/v1/shops.json") or []
    shops = [{"id": s.get("id"), "title": h.trim(s.get("title"), 80), "sales_channel": s.get("sales_channel")}
             for s in (d if isinstance(d, list) else [])[:30] if isinstance(s, dict)]
    try:
        configured = int(pf.shop_id())
    except h.CommerceError:
        configured = None
    return {"shops": shops, "configured_shop_id": configured}


@tool(name="products.list", category="printify", doc="printify/read.md")
def products_list(page: int = 1, limit: int = 20) -> dict:
    """The configured shop's products (PRINTIFY_SHOP_ID), compact: id, title, visible, blueprint and print
    provider, enabled variants, lowest price, updated.

    Args:
        page: 1 or more.
        limit: 1-20 per page.
    """
    page, limit = h.clamp(page, 1, 10_000, "page"), h.clamp(limit, 1, 20, "limit")
    d = pf.get(f"/v1/shops/{pf.shop_id()}/products.json", params={"page": page, "limit": limit}) or {}
    rows = []
    for p in (d.get("data") or [])[:limit]:
        if not isinstance(p, dict):
            continue
        on = [v for v in (p.get("variants") or []) if isinstance(v, dict) and v.get("is_enabled")]
        prices = [v.get("price") for v in on if isinstance(v.get("price"), (int, float))]
        rows.append({"id": p.get("id"), "title": h.trim(p.get("title"), 60), "visible": p.get("visible"),
                     "blueprint_id": p.get("blueprint_id"), "print_provider_id": p.get("print_provider_id"),
                     "variants_enabled": len(on), "min_price": _cents(min(prices)) if prices else None,
                     "updated": p.get("updated_at")})
    return {**_page(d), "products": rows}


@tool(name="product.get", category="printify", doc="printify/read.md")
def product_get(product_id: str, variant_ids: list[int] | None = None, offset: int = 0, limit: int = 40) -> dict:
    """One product in detail: title, description (first 1,000 characters), tags, blueprint, print provider,
    visibility, where it's published, and its enabled variants with id, title, price, cost (what Printify charges you)
    and sku. Money is in units (12.5 = 12.50).

    Args:
        product_id: the product's id (printify.products.list shows them).
        variant_ids: only these variants (e.g. the sizes you chose); empty = all enabled ones.
        offset, limit: paging over the variants, 1-100 a page (a tee in 3 colours and 6 sizes has 18).

    Returns:
        {..., "variants_enabled", "matches", "offset", "next_offset", "variants": [{"id", "title", "price", "cost",
        "sku"}], "currency", "images" (count), "mockups": [{"src", "position", "camera", "variant_ids",
        "variant_count", "is_default", "is_selected_for_publishing"}] (at most 20)}. "currency" is Printify's own field if the product data has one, else null: Printify
        doesn't say which currency product costs are in, so check before converting.
    """
    pid = h.check_hex_id(product_id, "product_id")
    offset, limit = h.clamp(offset, 0, 10_000, "offset"), h.clamp(limit, 1, 100, "limit")
    want = set()
    for v in variant_ids or []:
        try:
            want.add(int(v))
        except (TypeError, ValueError):
            raise h.CommerceError("variant_ids are numbers (printify.variants.list shows them)") from None
    p = pf.get(f"/v1/shops/{pf.shop_id()}/products/{pid}.json") or {}
    on = [v for v in (p.get("variants") or []) if isinstance(v, dict) and v.get("is_enabled")]
    rows = [v for v in on if not want or v.get("id") in want]
    page = rows[offset:offset + limit]
    ext = p.get("external") if isinstance(p.get("external"), dict) else {}
    return {"id": p.get("id"), "title": h.trim(p.get("title"), 140), "description": h.trim(p.get("description"), 1000),
            "tags": (p.get("tags") or [])[:20], "blueprint_id": p.get("blueprint_id"),
            "print_provider_id": p.get("print_provider_id"), "visible": p.get("visible"),
            "is_locked": p.get("is_locked"), "external_id": ext.get("id"), "variants_enabled": len(on),
            "matches": len(rows), "offset": offset,
            "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
            "variants": [{"id": v.get("id"), "title": h.trim(v.get("title"), 60), "price": _cents(v.get("price")),
                          "cost": _cents(v.get("cost")), "sku": v.get("sku")} for v in page],
            "currency": p.get("currency") if isinstance(p.get("currency"), str) else None,
            "images": len(p.get("images") or []), "mockups": _mockups(p, want),
            "created": p.get("created_at"), "updated": p.get("updated_at")}


MAX_MOCKUPS = 20


def _mockups(p: dict, want: set) -> list[dict]:
    """Printify's mockup images (at most 20): src (printify.mockup.fetch copies one into the image store), position
    and camera as Printify gives them, the variants shown (only the asked ones when variant_ids was given; else the
    first 10 and a count), default and publishing flags."""
    from urllib.parse import parse_qs, urlsplit
    rows = []
    for i in [x for x in (p.get("images") or []) if isinstance(x, dict) and isinstance(x.get("src"), str)][:MAX_MOCKUPS]:
        vids = [v for v in (i.get("variant_ids") or []) if isinstance(v, int)]
        shown = [v for v in vids if v in want] if want else vids[:10]
        camera = (parse_qs(urlsplit(i["src"]).query).get("camera_label") or [None])[0]
        rows.append({"src": i["src"][:500], "position": i.get("position"), "camera": camera, "variant_ids": shown,
                     "variant_count": len(vids), "is_default": bool(i.get("is_default")),
                     "is_selected_for_publishing": bool(i.get("is_selected_for_publishing"))})
    return rows


@tool(name="blueprints.list", category="printify", doc="printify/read.md")
def blueprints_list(query: str = "", offset: int = 0, limit: int = 25) -> dict:
    """Printify's catalogue of blank products (blueprints): id, title, brand, model. Filter by words in the title or
    brand; paged here, since the catalogue is over a thousand entries.

    Args:
        query: words to look for (case-insensitive), e.g. "mug" or "gildan"; empty = all.
        offset: skip this many matches.
        limit: 1-40 per page.
    """
    offset, limit = h.clamp(offset, 0, 100_000, "offset"), h.clamp(limit, 1, 40, "limit")
    q = str(query or "").strip().lower()[:60]
    d = pf.get("/v1/catalog/blueprints.json") or []
    hits = [b for b in (d if isinstance(d, list) else []) if isinstance(b, dict)
            and (not q or all(w in f"{b.get('title', '')} {b.get('brand', '')}".lower() for w in q.split()))]
    page = hits[offset:offset + limit]
    return {"matches": len(hits), "offset": offset,
            "next_offset": offset + len(page) if offset + len(page) < len(hits) else None,
            "blueprints": [{"id": b.get("id"), "title": h.trim(b.get("title"), 70), "brand": b.get("brand"),
                            "model": b.get("model")} for b in page]}


@tool(name="print_providers.list", category="printify", doc="printify/read.md")
def print_providers_list(blueprint_id: str) -> dict:
    """The print providers that make one blueprint: id, title, country.

    Args:
        blueprint_id: the blueprint's number (printify.blueprints.list shows them).
    """
    bid = h.check_id(blueprint_id, "blueprint_id")
    d = pf.get(f"/v1/catalog/blueprints/{bid}/print_providers.json") or []
    rows = [{"id": p.get("id"), "title": h.trim(p.get("title"), 60),
             "country": (p.get("location") or {}).get("country") if isinstance(p.get("location"), dict) else None}
            for p in (d if isinstance(d, list) else [])[:60] if isinstance(p, dict)]
    return {"blueprint_id": int(bid), "print_providers": rows}


@tool(name="print_provider.get", category="printify", doc="printify/read.md")
def print_provider_get(print_provider_id: str) -> dict:
    """One print provider (maker): where it is (city, region, country) and the blueprints it makes. The country
    print_providers.list can't show (Printify's per-blueprint list has no location, KB-0067) is here; use it to
    confirm a UK maker.

    Args:
        print_provider_id: the maker's number (printify.print_providers.list shows them).
    """
    pid = h.check_id(print_provider_id, "print_provider_id")
    d = pf.get(f"/v1/catalog/print_providers/{pid}.json") or {}
    loc = d.get("location") if isinstance(d.get("location"), dict) else {}
    bps = [b for b in (d.get("blueprints") or []) if isinstance(b, dict)]
    return {"id": d.get("id"), "title": h.trim(d.get("title"), 80),
            "location": {k: loc.get(k) for k in ("city", "region", "country")},  # not the street address
            "blueprints_total": len(bps),
            "blueprints": [{"id": b.get("id"), "title": h.trim(b.get("title"), 70), "brand": b.get("brand")}
                           for b in bps[:60]]}


_TAG = re.compile(r"<[^>]+>")


@tool(name="blueprint.get", category="printify", doc="printify/read.md")
def blueprint_get(blueprint_id: str) -> dict:
    """One blueprint (blank product): title, brand, model and Printify's description as plain text, which carries the
    fabric, fibre and weight lines a listing's material bullet needs.

    Args:
        blueprint_id: the blueprint's number (printify.blueprints.list shows them).
    """
    bid = h.check_id(blueprint_id, "blueprint_id")
    d = pf.get(f"/v1/catalog/blueprints/{bid}.json") or {}
    text = html.unescape(_TAG.sub("\n", str(d.get("description") or "")))
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return {"id": d.get("id"), "title": h.trim(d.get("title"), 100), "brand": d.get("brand"), "model": d.get("model"),
            "description": text[:3000], "description_chars": len(text)}


@tool(name="variants.list", category="printify", doc="printify/read.md")
def variants_list(blueprint_id: str, print_provider_id: str, colour: str = "", offset: int = 0, limit: int = 40) -> dict:
    """One blueprint's variants from one print provider: id, colour, size, and each print area's size in pixels (the
    width_px/height_px image.print_file needs). Costs aren't in Printify's catalogue: create a draft
    (printify.product.create) and read its variants' cost with printify.product.get.

    Args:
        blueprint_id, print_provider_id: from printify.blueprints.list / printify.print_providers.list.
        colour: only variants whose colour contains this (case-insensitive), e.g. "black".
        offset, limit: paging, 1-60 per page (a tee can have over a hundred colour/size variants).
    """
    bid, pid = h.check_id(blueprint_id, "blueprint_id"), h.check_id(print_provider_id, "print_provider_id")
    offset, limit = h.clamp(offset, 0, 10_000, "offset"), h.clamp(limit, 1, 60, "limit")
    want = str(colour or "").strip().lower()[:40]
    d = pf.get(f"/v1/catalog/blueprints/{bid}/print_providers/{pid}/variants.json") or {}
    raw = d.get("variants") if isinstance(d, dict) else d
    rows = []
    for v in raw if isinstance(raw, list) else []:
        if not isinstance(v, dict):
            continue
        opts = v.get("options") if isinstance(v.get("options"), dict) else {}
        if want and want not in str(opts.get("color") or "").lower():
            continue
        rows.append({"id": v.get("id"), "colour": opts.get("color"), "size": opts.get("size"),
                     "print_areas": [{"position": p.get("position"), "width_px": p.get("width"),
                                      "height_px": p.get("height")}
                                     for p in (v.get("placeholders") or [])[:6] if isinstance(p, dict)]})
    page = rows[offset:offset + limit]
    return {"blueprint_id": int(bid), "print_provider_id": int(pid), "matches": len(rows), "offset": offset,
            "next_offset": offset + len(page) if offset + len(page) < len(rows) else None, "variants": page,
            "cost": "not in the catalogue: create a draft, then printify.product.get shows each variant's cost"}


MAX_SHIPPING_PROFILES = 40


@tool(name="shipping", category="printify", doc="printify/read.md")
def shipping(blueprint_id: str, print_provider_id: str, country: str = "", variant_id: str = "") -> dict:
    """Delivery prices for one blueprint from one print provider, from Printify's catalogue: the handling time, and for
    each group of variants and countries the price of the first item and of each additional one. Prices are in units
    (4.5 = 4.50), in the currency Printify gives, never converted.

    Args:
        blueprint_id, print_provider_id: from printify.blueprints.list / printify.print_providers.list.
        country: only groups that deliver there (2 letters, e.g. "GB"); if no group names it, Printify's
            REST_OF_THE_WORLD group. A whole tee's table lists every variant for every group, so filter when you can.
        variant_id: only groups that include this variant.

    Returns:
        {"blueprint_id", "print_provider_id", "handling_time": {"value", "unit"}, "matches", "profiles":
        [{"variant_ids", "countries", "first_item", "additional_items", "currency"}]} (at most 40 groups).
    """
    bid, pid = h.check_id(blueprint_id, "blueprint_id"), h.check_id(print_provider_id, "print_provider_id")
    want = str(country or "").strip().upper()
    if want and not (len(want) == 2 and want.isalpha()):
        raise h.CommerceError('country is a 2-letter code, e.g. "GB"')
    vid = int(h.check_id(variant_id, "variant_id")) if str(variant_id or "").strip() else None
    d = pf.get(f"/v1/catalog/blueprints/{bid}/print_providers/{pid}/shipping.json") or {}
    raw = [p for p in (d.get("profiles") if isinstance(d, dict) else None) or [] if isinstance(p, dict)]
    if vid is not None:
        raw = [p for p in raw if vid in (p.get("variant_ids") or [])]
    if want:
        named = [p for p in raw if want in (p.get("countries") or [])]
        raw = named or [p for p in raw if "REST_OF_THE_WORLD" in (p.get("countries") or [])]

    def price(x):
        return x if isinstance(x, dict) else {}
    rows = [{"variant_ids": [v for v in (p.get("variant_ids") or []) if isinstance(v, int)],
             "countries": [str(c)[:20] for c in (p.get("countries") or [])][:60],
             "first_item": _cents(price(p.get("first_item")).get("cost")),
             "additional_items": _cents(price(p.get("additional_items")).get("cost")),
             "currency": price(p.get("first_item")).get("currency") or price(p.get("additional_items")).get("currency")}
            for p in raw]
    ht = d.get("handling_time") if isinstance(d, dict) and isinstance(d.get("handling_time"), dict) else {}
    return {"blueprint_id": int(bid), "print_provider_id": int(pid),
            "handling_time": {"value": ht.get("value"), "unit": ht.get("unit")}, "matches": len(rows),
            "profiles": rows[:MAX_SHIPPING_PROFILES]}


def _order_summary(o: dict) -> dict:
    items = [i for i in (o.get("line_items") or []) if isinstance(i, dict)]
    return {"id": o.get("id"), "status": o.get("status"), "created": o.get("created_at"),
            "total_price": _cents(o.get("total_price")), "total_shipping": _cents(o.get("total_shipping")),
            "total_tax": _cents(o.get("total_tax")), "item_count": sum(int(i.get("quantity") or 0) for i in items),
            "shop_order_id": (o.get("metadata") or {}).get("shop_order_id") if isinstance(o.get("metadata"), dict)
            else None}


@tool(name="orders.list", category="printify", doc="printify/read.md")
def orders_list(page: int = 1, limit: int = 10, status: str = "") -> dict:
    """The configured shop's Printify orders, newest first, compact: id, status, created, totals (in the shop's
    currency, e.g. 12.34), item count, the sales channel's order id. No delivery addresses or customer details.

    Args:
        page: 1 or more.
        limit: 1-10 per page (Printify's own maximum).
        status: optional filter, e.g. pending, on-hold, in-production, fulfilled, canceled.
    """
    page, limit = h.clamp(page, 1, 10_000, "page"), h.clamp(limit, 1, 10, "limit")
    params: dict = {"page": page, "limit": limit}
    if status:
        if not all(c.isalnum() or c in "-_" for c in status) or len(status) > 30:
            raise h.CommerceError("status must be a word like pending or fulfilled")
        params["status"] = status
    d = pf.get(f"/v1/shops/{pf.shop_id()}/orders.json", params=params) or {}
    return {**_page(d), "orders": [_order_summary(o) for o in (d.get("data") or [])[:limit] if isinstance(o, dict)]}


@tool(name="order.get", category="printify", doc="printify/read.md")
def order_get(order_id: str) -> dict:
    """One Printify order: status, totals, its items (product, variant, quantity, status, cost) and shipments
    (carrier, tracking number). No delivery address or customer details.

    Args:
        order_id: the order's id (printify.orders.list shows them).
    """
    oid = h.check_hex_id(order_id, "order_id")
    o = pf.get(f"/v1/shops/{pf.shop_id()}/orders/{oid}.json") or {}
    items = [{"product_id": i.get("product_id"), "variant_id": i.get("variant_id"), "quantity": i.get("quantity"),
              "status": i.get("status"), "cost": _cents(i.get("cost")), "shipping_cost": _cents(i.get("shipping_cost"))}
             for i in (o.get("line_items") or [])[:20] if isinstance(i, dict)]
    ships = [{"carrier": s.get("carrier"), "number": s.get("number"), "delivered": s.get("delivered_at")}
             for s in (o.get("shipments") or [])[:5] if isinstance(s, dict)]
    return {**_order_summary(o), "shipping_method": o.get("shipping_method"), "sent_to_production":
            o.get("sent_to_production_at"), "fulfilled": o.get("fulfilled_at"), "items": items, "shipments": ships}
