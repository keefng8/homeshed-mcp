"""image.carousel. See ../../capabilities/image/carousel.md."""
from __future__ import annotations

import base64

import httpx

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError, base_url, raise_for_backend
from tools.image.print_file import MAX_BYTES, NAME_RE

SIZES = {"4:5": "1080x1350", "9:16": "1080x1920"}
WATERMARK_STYLES = ("corner", "tiled", "both")


@tool(name="carousel", category="image", doc="image/carousel.md")
def carousel(image_names: list[str], shape: str = "4:5", background: str = "", watermark: str = "",
             watermark_style: str = "corner") -> dict:
    """Turn designs into social slides: each one centred on a 1080x1350 (4:5: Threads and Instagram feed posts and
    carousels) or 1080x1920 (9:16: reels and stories) canvas, on the design's own background colour so the slide
    reads as one piece. The slides are saved in the images "promo" folder; pass their names to threads.publish.

    Args:
        image_names: 1-10 designs by their saved_as name (local or paid).
        shape: "4:5" (default) or "9:16".
        background: "" uses each design's own corner colour (off-white for a transparent design); or "#rrggbb" for
            every slide.
        watermark: brand text (up to 40 characters) drawn small, bottom-right, about 60% opacity, in the clear space
            under the design, never over it. Only on these slides: the original designs stay clean. Leave it empty
            for images that go to a shop listing.
        watermark_style: "corner" (default): the small mark above. "tiled": the watermark text repeated diagonally
            across the whole slide, design included, faint (about 18% opacity, 3-4 rows on a 4:5 slide), so a crop
            can't remove it; for social posts. "both": tiled plus the corner mark. Needs watermark text.

    Returns:
        {"slides": [{"name", "width", "height", "bytes"}], "folder": "promo", "size"}.
    """
    if shape not in SIZES:
        raise ImageError('shape is "4:5" or "9:16"')
    style = watermark_style or "corner"
    if style not in WATERMARK_STYLES:
        raise ImageError(f"watermark_style is one of {list(WATERMARK_STYLES)}")
    if style != "corner" and not (watermark or "").strip():
        raise ImageError(f'watermark_style "{style}" needs the watermark text')
    names = [str(n).strip() for n in (image_names or []) if str(n).strip()]
    if not 1 <= len(names) <= 10:
        raise ImageError("give 1-10 image names")
    items = []
    for n in names:
        if not NAME_RE.match(n):
            raise ImageError(f"{n!r} isn't an image file name (use the image's saved_as)")
        paid = prov.output_dir() / n  # a paid provider's design is kept on the tool server
        if paid.is_file():
            if paid.stat().st_size > MAX_BYTES:
                raise ImageError(f"{n} is over 25 MB")
            items.append({"image_b64": base64.b64encode(paid.read_bytes()).decode()})
        else:
            items.append({"name": n})
    try:
        resp = httpx.post(f"{base_url()}/image/promo-slides", timeout=120,
                          json={"items": items, "size": SIZES[shape], "background": background or "",
                                "watermark": watermark or "", "watermark_style": style})
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(resp)
    return resp.json()
