"""image.print_file. See ../../capabilities/image/print_file.md."""
from __future__ import annotations

import base64
import re

import httpx

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError, base_url, raise_for_backend

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(png|jpg|jpeg|webp)$")
MAX_BYTES = 25 * 1024 * 1024


@tool(name="print_file", category="image", doc="image/print_file.md")
def print_file(image: str, width_px: int, height_px: int, variants: list[str] | None = None, colours: int = 0) -> dict:
    """Turn a design into print-ready files: a transparent PNG at the print area's exact size (a 5% margin), 300
    DPI, full colour (or `colours` flat colours), in a light-tee and a dark-tee version (a thin light keyline round the
    design so dark ink reads on a dark tee). The design must
    be on a plain background (white or one colour). Runs on the image service's CPU; files land in its
    "print-files" folder.

    Args:
        image: the design's file name: one the local image service made (image.status "saved_as"), or one a paid
            provider made (its "saved_as" on the tool server).
        width_px, height_px: the print area, 500-8000 px each (Printify gives it per product and position).
        variants: ["light", "dark"] (default both).
        colours: 0 keeps every colour (default; DTG prints full colour); 2-8 reduces to flat colours.

    Returns:
        {"files": {"light": {"name", "width", "height", "dpi", "colours"}, "dark": {...}}, "folder", "source",
        "checks"}. Download a file from the image service by its name.
    """
    if not NAME_RE.match(image or ""):
        raise ImageError("image must be a file name from image.generate / image.status (saved_as)")
    body = {"width_px": width_px, "height_px": height_px, "variants": variants or ["light", "dark"], "colours": colours}
    local = prov.output_dir() / image  # a paid provider's design is kept here on the tool server
    if local.is_file():
        if local.stat().st_size > MAX_BYTES:
            raise ImageError("that image is over 25 MB")
        body["image_b64"] = base64.b64encode(local.read_bytes()).decode()
    else:
        body["name"] = image
    try:
        resp = httpx.post(f"{base_url()}/image/print-file", json=body, timeout=120)
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(resp)
    return resp.json()
