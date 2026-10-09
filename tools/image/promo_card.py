"""image.promo_card. See ../../capabilities/image/promo_card.md."""
from __future__ import annotations

import base64
import re

import httpx

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError, base_url, raise_for_backend
from tools.image.print_file import MAX_BYTES, NAME_RE

SHAPES = {"9:16": "1080x1920", "4:5": "1080x1350"}
TEMPLATES = ("drop", "editorial")
WATERMARK_STYLES = ("tiled", "corner", "both")
STYLE_RE = re.compile(r"^[a-z0-9-]{1,30}$")  # a brand frame's style name, e.g. "neon-gold"


@tool(name="promo_card", category="image", doc="image/promo_card.md")
def promo_card(product_image: str, headline: str, price_gbp: float, template: str = "drop", benefit: str = "",
               link_text: str = "", kicker: str = "", shape: str = "9:16", frame: str = "", cutout: bool = False,
               watermark: str = "", watermark_style: str = "tiled") -> dict:
    """Make a branded promo card around a real product mockup: the mockup is the hero (about half the card, only
    scaled, never cropped or altered), with the brand's logo, the product name as a headline, a one-line benefit, the
    £ price, a "Shop now" button and the shop link. Saved in the images "promo" folder; pass its name to
    threads.publish.

    Args:
        product_image: the mockup by its saved name (e.g. from printify.mockup.fetch, or a local image).
        headline: the product's name (up to 60 characters).
        price_gbp: the real listed price in pounds, e.g. 24.99. Never a placeholder.
        template: "drop" (bold: dark navy, glowing frame, retro stripes, confetti; for younger buyers) or
            "editorial" (premium: cream, thin gold frame, serif headline; for gift buyers).
        benefit: one line that's true for the listing (up to 110 characters).
        link_text: the shop link as shown at the bottom (up to 60 characters).
        kicker: a short label (up to 20), e.g. "New drop"; only if true.
        shape: "9:16" (1080x1920, stories and reels; default) or "4:5" (1080x1350, the feed).
        frame: optional: a brand frame as the background instead of the drawn one: a style name ("neon-gold",
            "marble", "sunset", "gradient-glass", "synthwave", "halloween", "christmas", "valentines") or an image
            name from the images "frames" folder.
        cutout: False (default): the mockup as a framed photo tile, its plain backdrop margin trimmed so the
            product fills the frame (R&D's pick, 2026-10-07). True: lift it off a plain studio backdrop onto the
            scene (rough edges on a white tee on white); when the backdrop isn't plain it stays framed and the
            result says "cutout": false.
        watermark: brand text; empty = none.
        watermark_style: "tiled" (default: faint and diagonal across the card, under the text, price and button:
            about 7% on a light card, 12% on a dark one), "corner" (small, under the mockup) or "both".

    The supplier's name is refused in any text: the card never names it.

    Returns:
        {"name", "width", "height", "bytes", "folder": "promo", "size", "template", "hero_share", "frame", "cutout"}.
    """
    if shape not in SHAPES:
        raise ImageError('shape is "9:16" or "4:5"')
    if template not in TEMPLATES:
        raise ImageError('template is "drop" or "editorial"')
    if watermark_style not in WATERMARK_STYLES:
        raise ImageError(f"watermark_style is one of {list(WATERMARK_STYLES)}")
    name = (product_image or "").strip()
    if not NAME_RE.match(name):
        raise ImageError(f"{name!r} isn't an image file name (use the image's saved name)")
    frame = (frame or "").strip()
    if frame and not (NAME_RE.match(frame) or STYLE_RE.match(frame)):
        raise ImageError(f"{frame!r} isn't a frame style (e.g. \"neon-gold\") or an image name from the frames folder")
    paid = prov.output_dir() / name  # a mockup or paid design kept on the tool server goes as bytes
    if paid.is_file():
        if paid.stat().st_size > MAX_BYTES:
            raise ImageError(f"{name} is over 25 MB")
        product = {"image_b64": base64.b64encode(paid.read_bytes()).decode()}
    else:
        product = {"name": name}
    try:
        resp = httpx.post(f"{base_url()}/image/promo-card", timeout=120, json={
            "product": product, "headline": headline or "", "price_gbp": price_gbp, "template": template,
            "size": SHAPES[shape], "benefit": benefit or "", "link_text": link_text or "", "kicker": kicker or "",
            "frame": frame, "cutout": bool(cutout), "watermark": watermark or "", "watermark_style": watermark_style})
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(resp)
    return resp.json()
