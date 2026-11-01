"""printify.product.update. See ../../capabilities/printify/update.md.

2026-10-07: every draft needed hand fixes in Printify before
publishing (delivery and material bullets, tag swaps, prices). This edits an UNPUBLISHED draft only. Anything on a
sales channel, locked mid-publish, waiting for the owner's publish approval or published today is refused. A price
never goes under the margin floor, and every edit keeps the old values, so it can be undone.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from pathlib import Path

from paths import data_path
from registry import tool
from tools.commerce import _http as h
from tools.printify import _client as pf
from tools.printify.write import MAX_TAGS

EDITS_FILE = Path(os.environ.get("PRINTIFY_EDITS_FILE") or data_path("usage/printify_edits.json"))
SKU_RE = re.compile(r"^[A-Za-z0-9._/-]{1,32}$")  # Etsy takes SKUs of up to 32 characters
_lock = threading.Lock()

# Etsy UK seller fees (R&D, 2026-10-07; the shop app's fee config): transaction 6.5%, payment processing 4% + 20p,
# regulatory 0.48%, listing 16p, VAT 20% on Etsy's fees. The buyer pays delivery at cost, and the % fees count on it.
PCT_FEES = (650 + 400 + 48) / 10000
VAT = 0.20
FIXED_FEES_PENCE = 20 + 16


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


HOODIE_BLUEPRINTS = {77, 91}  # Printify's Gildan 18500 hoodie; AWDIS JH050 full-zip hoodie (the owner, 31 Oct)


def min_margin(blueprint_id=None) -> float:
    """The owner, 2026-10-09 ("£53 seems a little expensive for one hoodie ... half the %"; "everything else slightly
    lower than 30 ... 20%"): 15% on hoodies (PRINTIFY_MIN_MARGIN_HOODIE), 20% on everything else (PRINTIFY_MIN_MARGIN).
    It was 30% for all."""
    if blueprint_id in HOODIE_BLUEPRINTS:
        return _env_float("PRINTIFY_MIN_MARGIN_HOODIE", 0.15)
    return _env_float("PRINTIFY_MIN_MARGIN", 0.20)


def floor_pence(cost_pence: int, blueprint_id=None) -> int:
    """The lowest price that still keeps min_margin(blueprint_id) of it after the cost and Etsy's fees with VAT.
    Delivery is PRINTIFY_DELIVERY_ESTIMATE_PENCE (default 400): a high guess keeps the floor on the safe side."""
    margin = min_margin(blueprint_id)
    delivery = _env_float("PRINTIFY_DELIVERY_ESTIMATE_PENCE", 400)
    pct = PCT_FEES * (1 + VAT)
    fixed = FIXED_FEES_PENCE * (1 + VAT) + pct * delivery
    return math.ceil((cost_pence + fixed) / (1 - pct - margin))


def _daily_max() -> int:
    try:
        return max(0, min(500, int(os.environ.get("PRINTIFY_EDIT_DAILY_MAX") or 50)))
    except ValueError:
        return 50


def _edits() -> list[dict]:
    try:
        rows = json.loads(EDITS_FILE.read_text(encoding="utf-8")).get("edits") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def _record(row: dict) -> None:
    rows = _edits() + [row]
    EDITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EDITS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"edits": rows[-500:]}, indent=1), encoding="utf-8")
    os.replace(tmp, EDITS_FILE)


def _not_a_draft(pid: str, product: dict) -> str | None:
    import printify_pending
    if product.get("external"):
        return "it's on a sales channel (published)"
    if product.get("is_locked"):
        return "Printify is publishing it right now"
    if any(i.get("product_id") == pid for i in printify_pending.list_pending()):
        return "a publish is waiting for the owner's approval: decline it first"
    if any(r.get("product_id") == pid for r in printify_pending.published_today()):
        return "it was published today"
    return None


def enabled_of(variants: list[dict] | None) -> dict[int, bool]:
    """{variant: on/off} from the asked-for variants' "enabled" (2026-10-09, the shop app: turn sizes off on a live
    product without touching SKUs). Checked by check_input first."""
    return {v["id"]: v["enabled"] for v in variants or [] if isinstance(v, dict) and isinstance(v.get("enabled"), bool)}


def check_input(title: str, description: str, tags: list[str] | None, variants: list[dict] | None,
                skus_allowed: bool = True) -> tuple[str, str, list[str] | None, dict[int, int], dict[int, str]]:
    """The asked-for edit, checked: (title, description, tags, {variant: price}, {variant: sku}); a variant's
    "enabled" (true/false) is read by enabled_of. Shared with printify.product.update_live, which passes
    skus_allowed=False."""
    title, description = (title or "").strip(), (description or "").strip()
    if len(title) > 140:
        raise h.CommerceError("title must be at most 140 characters")
    if len(description) > 5000:
        raise h.CommerceError("description must be at most 5,000 characters")
    if tags is not None:
        tags = [str(t).strip() for t in tags if str(t).strip()]
        if len(tags) > MAX_TAGS or any(len(t) > 20 for t in tags):
            raise h.CommerceError(f"tags: at most {MAX_TAGS}, each up to 20 characters (Etsy's limits)")
    if not (title or description or tags is not None or variants):
        raise h.CommerceError("nothing to change: give a title, description, tags or variants")
    prices: dict[int, int] = {}
    skus: dict[int, str] = {}
    for v in variants or []:
        vid, price, sku = (v or {}).get("id"), (v or {}).get("price_pence"), (v or {}).get("sku")
        enabled = (v or {}).get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise h.CommerceError(f"variant {vid}: enabled is true or false")
        if not isinstance(vid, int) or (price is None and sku is None and enabled is None) or (
                price is not None and (not isinstance(price, int) or not 100 <= price <= 100_000)):
            raise h.CommerceError("each variant needs an integer id and price_pence of 100-100000, a sku, or "
                                  "enabled (true/false)")
        if price is not None:
            prices[vid] = price
        if sku is not None:
            if not skus_allowed:
                raise h.CommerceError("a live product's SKUs can't change: Printify matches Etsy orders to products by "
                                      "SKU, so a new one can stop orders arriving")
            if not isinstance(sku, str) or not SKU_RE.fullmatch(sku.strip()):
                raise h.CommerceError(f"variant {vid}: a sku is 1-32 letters, digits, dots, dashes, slashes or "
                                      "underscores")
            skus[vid] = sku.strip()
    return title, description, tags, prices, skus


def changes(product: dict, title: str, description: str, tags: list[str] | None, prices: dict[int, int],
            skus: dict[int, str], enabled: dict[int, bool] | None = None) -> tuple[dict, dict]:
    """(Printify's PUT body, the old values) for what differs from the product now; ({}, {}) when it's as asked. No
    price under the floor, and never every variant switched off."""
    enabled = enabled or {}
    body, old = {}, {}
    if title and title != product.get("title"):
        body["title"], old["title"] = title, product.get("title")
    if description and description != (product.get("description") or ""):
        body["description"], old["description"] = description, product.get("description")
    if tags is not None and tags != (product.get("tags") or []):
        body["tags"], old["tags"] = tags, product.get("tags")
    current = {v["id"]: v for v in product.get("variants") or [] if isinstance(v, dict) and isinstance(v.get("id"), int)}
    for vid in [*prices, *skus, *enabled]:
        if vid not in current:
            raise h.CommerceError(f"variant {vid} isn't on this product")
    new_on = {vid: on for vid, on in enabled.items() if on != bool(current[vid].get("is_enabled", True))}
    if new_on and not any(new_on.get(vid, bool(v.get("is_enabled", True))) for vid, v in current.items()):
        raise h.CommerceError("that would switch off every variant: a product needs at least one on sale")
    for vid, price in prices.items():
        cost = current[vid].get("cost")
        bp = product.get("blueprint_id")
        if isinstance(cost, (int, float)) and price < (low := floor_pence(int(cost), bp)):
            raise h.CommerceError(f"variant {vid}: {price}p is under the floor of {low}p (a {min_margin(bp):.0%} "
                                  f"margin after the {int(cost)}p cost and Etsy's fees with VAT)")
    new_prices = {vid: p for vid, p in prices.items() if p != current[vid].get("price")}
    new_skus = {vid: s for vid, s in skus.items() if s != current[vid].get("sku")}
    if new_prices or new_skus or new_on:  # Printify takes the whole variant list on an edit: the rest go back as they are
        body["variants"] = [{"id": vid, "price": new_prices.get(vid, v.get("price")),
                             "is_enabled": new_on.get(vid, v.get("is_enabled", True)),
                             **({"sku": new_skus[vid]} if vid in new_skus else {})}
                            for vid, v in current.items()]
    if new_prices:
        old["prices"] = {str(vid): current[vid].get("price") for vid in new_prices}
    if new_skus:
        old["skus"] = {str(vid): current[vid].get("sku") for vid in new_skus}
    if new_on:
        old["enabled"] = {str(vid): bool(current[vid].get("is_enabled", True)) for vid in new_on}
    return body, old


def fields_of(body: dict, old: dict) -> list[str]:
    return sorted([k for k in body if k != "variants"] + [k for k in ("prices", "skus", "enabled") if k in old])


MAX_PRINT_AREAS = 10
# Each number's allowed range (2026-10-31): scale is the image's width as a share of the print area's
AREA_RANGES = {"scale": (0.05, 2.0), "x": (0.0, 1.0), "y": (0.0, 1.0), "angle": (-180, 180)}


def check_print_areas(areas) -> list[dict]:
    """The asked-for print_areas, checked (shape and ranges only; the positions are checked against the blueprint
    once the draft is read): [{"position", "print_file", "scale", "x", "y", "angle"}]."""
    from tools.printify.write import check_print_file
    if not isinstance(areas, list) or not 1 <= len(areas) <= MAX_PRINT_AREAS:
        raise h.CommerceError(f"print_areas must be a list of 1-{MAX_PRINT_AREAS} {{position, print_file, scale, x, y}}")
    out = []
    for i, a in enumerate(areas, 1):
        if not isinstance(a, dict):
            raise h.CommerceError(f"print area {i}: give {{position, print_file, scale, x, y, angle?}}")
        pos = a.get("position")
        if not isinstance(pos, str) or not pos.strip():
            raise h.CommerceError(f"print area {i}: position is a print position name, like front or back")
        row = {"position": pos.strip().lower(), "print_file": check_print_file(a.get("print_file"))}
        for key, (low, high) in AREA_RANGES.items():
            v = a.get(key, 0 if key == "angle" else None)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
                raise h.CommerceError(f"print area {i} ({row['position']}): {key} must be a number from {low} to {high}")
            row[key] = int(v) if key == "angle" else float(v)
        out.append(row)
    return out


def blueprint_positions(product: dict) -> set[str]:
    """The print positions the draft's blueprint has at its print provider (Printify's catalogue, as
    printify.variants.list reads it)."""
    bid, ppid = product.get("blueprint_id"), product.get("print_provider_id")
    if not isinstance(bid, int) or not isinstance(ppid, int):
        raise h.CommerceError("the draft doesn't say its blueprint and print provider, so its print positions can't "
                              "be checked")
    d = pf.get(f"/v1/catalog/blueprints/{bid}/print_providers/{ppid}/variants.json") or {}
    raw = d.get("variants") if isinstance(d, dict) else d
    return {str(p.get("position")).lower() for v in raw if isinstance(raw, list) and isinstance(v, dict)
            for p in v.get("placeholders") or [] if isinstance(p, dict) and p.get("position")}


def print_areas_body(product: dict, areas: list[dict], image_ids: list[str]) -> list[dict]:
    """Printify's print_areas for the asked-for areas, on the draft's own variant ids (every variant its print areas
    cover now, or else all of them). Two images on one position share its placeholder."""
    vids = sorted({vid for pa in product.get("print_areas") or [] if isinstance(pa, dict)
                   for vid in pa.get("variant_ids") or [] if isinstance(vid, int)})
    vids = vids or [v["id"] for v in product.get("variants") or [] if isinstance(v, dict) and isinstance(v.get("id"), int)]
    placeholders: dict[str, list] = {}
    for a, image_id in zip(areas, image_ids):
        placeholders.setdefault(a["position"], []).append(
            {"id": image_id, "x": a["x"], "y": a["y"], "scale": a["scale"], "angle": a["angle"]})
    return [{"variant_ids": vids, "placeholders": [{"position": p, "images": imgs} for p, imgs in placeholders.items()]}]


@tool(name="product.update", category="printify", doc="printify/update.md")
def product_update(product_id: str, title: str = "", description: str = "", tags: list[str] | None = None,
                   variants: list[dict] | None = None, print_areas: list[dict] | None = None) -> dict:
    """Edit an UNPUBLISHED Printify draft: its title, description, tags, variant prices and SKUs, and its print areas
    (which design goes where). Drafts only
    (anything on a sales channel, mid-publish, waiting for publish approval or published today is refused). No price
    under the margin floor (20%, hoodies 15%) after Etsy's fees. Every edit is audited and keeps the old values. Off unless the
    owner's switch "Let apps create and edit Printify drafts" is on. A published product: printify.product.update_live.

    Args:
        product_id: the draft's id (printify.products.list).
        title: the new title, 1-140 characters; empty keeps it.
        description: the new description, up to 5,000 characters; empty keeps it.
        tags: the new full tag list (up to 13, each up to 20 characters: Etsy's limits); omit to keep them.
        variants: [{"id": variant id, "price_pence": new price, "sku": new SKU, "enabled": true/false}] for the
            variants to change (any of the keys; a SKU is 1-32 letters, digits, dots, dashes, slashes or underscores;
            "enabled": false takes a size or colour off sale, never all of them); the others keep theirs.
        print_areas: [{"position": "front"/"back"/... (one of the blueprint's print positions), "print_file": a PNG
            name from image.print_file, "scale": 0.05-2.0 (the image's width as a share of the area's), "x", "y": 0-1
            (the image's centre; 0.5, 0.5 is the middle), "angle": -180 to 180 (default 0)}]: REPLACES all the
            draft's print areas (each file is uploaded to Printify first). Prices and variants stay as they are.

    Returns:
        {"product_id", "changed": [fields], "old": {...the values before}, "published": false, "edits_today",
        "daily_max"}; "changed" is empty (and nothing is sent) when the draft is already as asked. "note" says when
        Printify didn't keep a SKU it was sent.
    """
    pid = h.check_hex_id(product_id, "product_id")
    areas = check_print_areas(print_areas) if print_areas is not None else None
    if areas and not (title or description or tags is not None or variants):
        title, description, tags, prices, skus = "", "", None, {}, {}  # only the print areas change
    else:
        title, description, tags, prices, skus = check_input(title, description, tags, variants)
    h.require(pf.SETTING, "Printify")
    h.require(pf.WRITE_SETTING, "Editing Printify drafts")
    shop = pf.shop_id()
    product = pf.get(f"/v1/shops/{shop}/products/{pid}.json") or {}
    if not isinstance(product, dict) or not product.get("id"):
        raise h.CommerceError("Printify has no such product in this shop")
    if why := _not_a_draft(pid, product):
        raise h.CommerceError(f"only unpublished drafts can be edited, and this one isn't: {why}")
    body, old = changes(product, title, description, tags, prices, skus, enabled_of(variants))
    if areas:
        allowed = blueprint_positions(product)
        if bad := sorted({a["position"] for a in areas} - allowed):
            raise h.CommerceError(f"print position {', '.join(bad)} isn't on this blueprint at this print provider: "
                                  f"it has {', '.join(sorted(allowed)) or 'none listed'}")
        body["print_areas"] = []  # filled with the uploaded image ids below, under the daily limit
        old["print_areas"] = product.get("print_areas") or []  # the whole old layout, so it can be put back
    if not body:
        return {"product_id": pid, "changed": [], "old": {}, "published": False, "note": "already as asked: nothing sent"}
    import clients
    who = clients.current_client.get() or "owner"
    fields = fields_of(body, old)
    with _lock:
        today = [r for r in _edits() if time.time() - float(r.get("at") or 0) < 86400]
        if len(today) >= _daily_max():
            raise h.CommerceError(f"{len(today)} draft edits in the last 24 hours: the daily limit ({_daily_max()}) is reached")
        if areas:
            from tools.printify.write import upload_print_file
            ids = {f: upload_print_file(f) for f in dict.fromkeys(a["print_file"] for a in areas)}  # each file once
            body["print_areas"] = print_areas_body(product, areas, [ids[a["print_file"]] for a in areas])
        sent = pf.put(f"/v1/shops/{shop}/products/{pid}.json", body)
        _record({"at": time.time(), "product_id": pid, "by": who, "fields": fields, "old": old})
    clients._audit("printify draft edited", None if who == "owner" else who, product=pid, fields=",".join(fields))
    out = {"product_id": pid, "changed": fields, "old": old, "published": False, "edits_today": len(today) + 1,
           "daily_max": _daily_max()}
    if "skus" in old and isinstance(sent, dict) and isinstance(sent.get("variants"), list):
        kept = {v.get("id"): v.get("sku") for v in sent["variants"] if isinstance(v, dict)}
        missed = [vid for vid in map(int, old["skus"]) if kept.get(vid) != skus[vid]]
        if missed:  # Printify's docs list sku on a variant but don't promise it can be edited: say so, never assume
            out["note"] = f"Printify didn't keep the new SKU for variant(s) {missed}: it may not allow SKU edits"
    return out
