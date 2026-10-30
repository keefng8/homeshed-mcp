"""image.preview. See ../../capabilities/image/preview.md."""
from __future__ import annotations

import base64
import re

import httpx

from registry import tool
from tools._helper_auth import helper_headers
from tools.image.generate import ImageError, base_url, raise_for_backend

PRINT_NAME = re.compile(r"^\d{8}-\d{6}-\d+\.png$")
GARMENT = re.compile(r"^(sand|white|navy|black|#[0-9a-fA-F]{6})$", re.IGNORECASE)


@tool(name="preview", category="image", doc="image/preview.md")
def preview(image: str, garment: str = "sand", print_area: str = "front", show_area: bool = True,
            include_image: bool = False) -> dict:
    """A print file on a plain flat tee, so the owner rates exactly what will be printed: the file at its true size
    (300 DPI on a size-M tee), no person or model. Free, on the image service's CPU, about a second.

    Args:
        image: a print file's name (image.print_file's files.light.name or files.dark.name: use the dark one for
            navy or black).
        garment: "sand" (default), "white", "navy", "black", or any "#rrggbb" (Printify lists each colour's hex).
        print_area: "front" (default) or "back".
        show_area: a faint dashed outline of the whole print area (default true).
        include_image: also return the JPEG itself as "image_b64" with "image_mime" (about 150-300 KB), for an app
            that keeps its own copy.

    Returns:
        {"name", "folder": "previews", "ref": "previews/<name>", "width", "height", "garment", "garment_rgb",
        "print_area", "print_box": [x, y, w, h], "true_size_in": [w, h], "scaled_to_fit", "source"}. A 1024 px JPEG;
        `ref` works wherever an image is named ("folder/name.jpg").
    """
    if not PRINT_NAME.match(image or ""):
        raise ImageError("image is a print file's name from image.print_file (files.light.name or files.dark.name)")
    if not GARMENT.match(garment or ""):
        raise ImageError('garment is "sand", "white", "navy", "black" or a "#rrggbb" colour')
    if print_area not in ("front", "back"):
        raise ImageError('print_area is "front" or "back"')
    body = {"name": image, "garment": garment, "print_area": print_area, "show_area": show_area}
    try:
        resp = httpx.post(f"{base_url()}/image/garment-preview", json=body, timeout=60, headers=helper_headers())
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(resp)
    out = resp.json()
    if include_image:  # an app whose own page can't reach the image service (a shop app, 2026-10-08)
        from tools.commerce._http import CommerceError
        from tools.image.refs import image_bytes
        try:
            out |= {"image_b64": base64.b64encode(image_bytes(out["ref"], 4 * 1024 * 1024)).decode(),
                    "image_mime": "image/jpeg"}
        except CommerceError as exc:
            raise ImageError(f"the preview was made ({out['ref']}) but couldn't be fetched: {exc}") from None
    return out
