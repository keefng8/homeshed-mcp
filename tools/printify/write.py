"""printify.product.create. See ../../capabilities/printify/write.md.

The owner, 2026-10-07: products for an app's listings goal. This makes UNPUBLISHED drafts only. Since 2026-10-07 the
configured shop is connected to Etsy (sales channel etsy), so Printify can publish to it; publishing is a separate step
the owner approves each time, and it isn't built yet.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path

import httpx

from paths import data_path
from registry import tool
from tools.commerce import _http as h
from tools.printify import _client as pf

POSTS_FILE = Path(os.environ.get("PRINTIFY_CREATED_FILE") or data_path("usage/printify_created.json"))
MAX_VARIANTS, MAX_TAGS, MAX_IMAGE_BYTES = 100, 13, 25 * 1024 * 1024
POSITIONS = ("front", "back")
_lock = threading.Lock()


def _daily_max() -> int:
    try:
        return max(0, min(200, int(os.environ.get("PRINTIFY_DAILY_MAX") or 20)))
    except ValueError:
        return 20


def _today() -> list[dict]:
    try:
        rows = json.loads(POSTS_FILE.read_text(encoding="utf-8")).get("created") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    return [r for r in rows if isinstance(r, dict) and time.time() - float(r.get("at") or 0) < 86400]


def _record(row: dict) -> None:
    rows = _today() + [row]
    POSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = POSTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"created": rows[-500:]}, indent=1), encoding="utf-8")
    os.replace(tmp, POSTS_FILE)


def _print_file_bytes(name: str) -> bytes:
    """The print file, from the image service (image.print_file wrote it there)."""
    from tools.image.generate import ImageError, base_url
    try:
        resp = httpx.get(f"{base_url()}/image/files/{name}", timeout=60)
    except (httpx.HTTPError, ImageError) as exc:
        raise h.CommerceError(f"couldn't fetch the print file from the image service ({type(exc).__name__})") from None
    if resp.status_code != 200:
        raise h.CommerceError(f"the image service has no print file {name!r} (HTTP {resp.status_code})")
    if len(resp.content) > MAX_IMAGE_BYTES:
        raise h.CommerceError("the print file is over 25 MB")
    return resp.content


@tool(name="product.create", category="printify", doc="printify/write.md")
def product_create(title: str, description: str, blueprint_id: int, print_provider_id: int, variants: list[dict],
                   print_file: str, position: str = "front", tags: list[str] | None = None) -> dict:
    """Make an UNPUBLISHED product in the owner's Printify shop from a print file (image.print_file), so Printify
    renders its mockups. Nothing is published or sold. Off until the owner switches "Let apps create Printify
    products" on; at most PRINTIFY_DAILY_MAX a day (default 20); every one audited.

    Args:
        title: 1-140 characters.
        description: up to 5,000 characters (plain text or simple HTML).
        blueprint_id, print_provider_id: from printify.blueprints.list / printify.print_providers.list.
        variants: [{"id": variant id, "price_pence": price in the shop's currency's minor unit}], 1-100.
        print_file: the file name image.print_file returned (light or dark version).
        position: "front" (default) or "back".
        tags: up to 13 tags.

    Returns:
        {"product_id", "image_id", "title", "variants", "published": false, "created_today", "daily_max"}.
    """
    title, description = (title or "").strip(), (description or "").strip()
    if not 1 <= len(title) <= 140:
        raise h.CommerceError("title must be 1-140 characters")
    if len(description) > 5000:
        raise h.CommerceError("description must be at most 5,000 characters")
    if position not in POSITIONS:
        raise h.CommerceError(f"position must be one of {', '.join(POSITIONS)}")
    tags = [str(t).strip()[:20] for t in (tags or []) if str(t).strip()][:MAX_TAGS]
    if not isinstance(variants, list) or not 1 <= len(variants) <= MAX_VARIANTS:
        raise h.CommerceError(f"variants must be a list of 1-{MAX_VARIANTS} {{id, price_pence}}")
    vs = []
    for v in variants:
        vid, price = (v or {}).get("id"), (v or {}).get("price_pence")
        if not isinstance(vid, int) or not isinstance(price, int) or not 100 <= price <= 100_000:
            raise h.CommerceError("each variant needs an integer id and price_pence of 100-100000")
        vs.append({"id": vid, "price": price, "is_enabled": True})
    h.check_id(blueprint_id, "blueprint_id")
    h.check_id(print_provider_id, "print_provider_id")
    if not (print_file or "").endswith(".png") or "/" in print_file or ".." in print_file:
        raise h.CommerceError("print_file must be a PNG name from image.print_file")
    h.require(pf.SETTING, "Printify")
    h.require(pf.WRITE_SETTING, "Creating Printify products")
    shop = pf.shop_id()
    import clients
    who = clients.current_client.get() or "owner"
    with _lock:
        made = len(_today())
        if made >= _daily_max():
            raise h.CommerceError(f"{made} products made in the last 24 hours: the daily limit ({_daily_max()}) is reached")
        raw = _print_file_bytes(print_file)
        up = pf.post("/v1/uploads/images.json", {"file_name": print_file, "contents": base64.b64encode(raw).decode()})
        image_id = up.get("id") if isinstance(up, dict) else None
        if not image_id:
            raise h.CommerceError("Printify didn't return an image id for the upload")
        body = {"title": title, "description": description, "blueprint_id": int(blueprint_id),
                "print_provider_id": int(print_provider_id), "variants": vs, "tags": tags,
                "print_areas": [{"variant_ids": [v["id"] for v in vs],
                                 "placeholders": [{"position": position,
                                                   "images": [{"id": image_id, "x": 0.5, "y": 0.5, "scale": 1, "angle": 0}]}]}]}
        product = pf.post(f"/v1/shops/{shop}/products.json", body)
        pid = product.get("id") if isinstance(product, dict) else None
        if not pid:
            raise h.CommerceError("Printify didn't return a product id")
        _record({"at": time.time(), "product_id": str(pid), "by": who})
    clients._audit("printify product created (unpublished)", None if who == "owner" else who, product=str(pid))
    return {"product_id": str(pid), "image_id": str(image_id), "title": title, "variants": len(vs), "published": False,
            "created_today": made + 1, "daily_max": _daily_max()}
