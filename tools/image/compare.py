"""image.compare. See ../../capabilities/image/compare.md."""
from __future__ import annotations

import base64

import httpx

from registry import tool
from tools._helper_auth import helper_headers
from tools.image.generate import ImageError, base_url, raise_for_backend

MAX_BYTES = 20 * 1024 * 1024


@tool(name="compare", category="image", doc="image/compare.md")
def compare(image: str, reference: str, region: str = "print") -> dict:
    """Is a scene's print exactly the reference's print, and does the tee stand out from the background? For a
    lifestyle scene an image model made from the real mockup. Free, on the image service's CPU, about 2-4 s.

    Args:
        image: the scene: an image name (saved_as), "folder/name.png", a Printify mockup address, or an Etsy listing
            photo's address (url_fullxfull on i.etsystatic.com, from etsy.listing.get): is the live photo the mockup?
        reference: what the print must look like: the mockup the scene was made from, the print file or the design
            (same forms as image).
        region: "print" (default): only the print on the reference counts; "all": the whole reference.

    Returns:
        {"ok", "print_ok", "found", "score", "structure", "colour", "weakest_cell": {"score", "where"}, "added_text",
        "tee": {"tee_colour", "low_contrast_outline", "edge_contrast", "blends"}, "box": {"x", "y", "w", "h"},
        "quad", "inliers", "note", "ms"}. ok: the print is as printed AND the tee stands out; print_ok: the print alone.
    """
    if region not in ("print", "all"):
        raise ImageError('region is "print" or "all"')
    from tools.commerce._http import CommerceError
    from tools.image.refs import image_bytes
    try:
        scene, ref = image_bytes(image, MAX_BYTES, etsy_photos=True), image_bytes(reference, MAX_BYTES)
    except CommerceError as exc:
        raise ImageError(str(exc)) from None
    body = {"image_b64": base64.b64encode(scene).decode(), "reference_b64": base64.b64encode(ref).decode(),
            "region": region}
    try:
        resp = httpx.post(f"{base_url()}/image/compare", json=body, timeout=90, headers=helper_headers())
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(resp)
    return resp.json()
