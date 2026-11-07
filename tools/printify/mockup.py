"""printify.mockup.fetch. See ../../capabilities/printify/mockup.md.

Printify renders product mockups (the shirt on a model, the mug on a table). This copies one into the tool server's
image store, so promo tools that take image names (image.carousel with its watermark, threads.publish) can use the real
product. Read-only towards Printify; it only fetches a URL that the product itself lists, on Printify's image host.
"""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlsplit

import httpx

from registry import tool
from tools.commerce import _http as h
from tools.printify import _client as pf

# Printify's two image hosts: product.get lists mockups on images.printify.com (seen 2026-10-07), older ones on
# images-api. Either way the src must be one the product itself lists.
MOCKUP_HOSTS = ("images.printify.com", "images-api.printify.com")
MAX_MOCKUP_BYTES = 15 * 1024 * 1024


@tool(name="mockup.fetch", category="printify", doc="printify/mockup.md")
def mockup_fetch(product_id: str, src: str) -> dict:
    """Copy one of a product's Printify mockups into the image store and return its image name, for image.carousel
    (slides with the brand watermark) and threads.publish. Only a src that printify.product.get lists for that
    product, on Printify's image host. It always downloads the mockup: the same picture returns the copy already made,
    a changed one (a replaced print keeps its address) a new copy under a new name.

    Args:
        product_id: the product's id.
        src: one of its mockups' "src" (printify.product.get, "mockups").

    Returns:
        {"name", "product_id", "bytes", "mime", "width", "new"}: "name" goes in image_names.
    """
    from tools.image import providers as prov
    from tools.threads.publish import _width
    pid = h.check_hex_id(product_id, "product_id")
    src = (src or "").strip()
    u = urlsplit(src)
    if u.scheme != "https" or u.hostname not in MOCKUP_HOSTS or u.username or u.password or len(src) > 1000:
        raise h.CommerceError("src must be a mockup address on Printify's image host, exactly as printify.product.get "
                              "lists it")
    p = pf.get(f"/v1/shops/{pf.shop_id()}/products/{pid}.json") or {}
    if src not in {i.get("src") for i in (p.get("images") or []) if isinstance(i, dict)}:
        raise h.CommerceError("that src isn't one of this product's mockups (printify.product.get lists them)")
    try:
        with httpx.stream("GET", src, timeout=60, follow_redirects=False,
                          headers={"User-Agent": pf.USER_AGENT}) as r:
            if r.status_code != 200:
                raise h.CommerceError(f"Printify's image host answered HTTP {r.status_code}")
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > MAX_MOCKUP_BYTES:
                    raise h.CommerceError("that mockup is over 15 MB")
    except httpx.HTTPError as exc:
        raise h.CommerceError(f"Printify's image host didn't answer ({type(exc).__name__})") from None
    mime = prov.sniff_mime(data)
    if mime not in ("image/jpeg", "image/png"):
        raise h.CommerceError("that mockup isn't a JPEG or PNG")
    # The address stays the same when a print is replaced, so only the picture itself can say it's changed (KB-0152).
    # A new picture gets a new name: a card approved on the old name keeps showing what was approved.
    stem = f"printify-{pid}-{hashlib.sha256(src.encode()).hexdigest()[:8]}"
    store = prov.output_dir()
    for old in store.glob(f"{stem}*"):
        if old.read_bytes() == data:
            return {"name": old.name, "product_id": pid, "bytes": len(data), "mime": mime, "width": _width(data),
                    "new": False}
    name = f"{stem}-{hashlib.sha256(data).hexdigest()[:8]}.{'png' if mime == 'image/png' else 'jpg'}"
    store.mkdir(parents=True, exist_ok=True)
    tmp = store / f".{name}.part"
    tmp.write_bytes(data)
    os.replace(tmp, store / name)
    return {"name": name, "product_id": pid, "bytes": len(data), "mime": mime, "width": _width(data), "new": True}
