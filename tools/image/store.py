"""image.store. See ../../capabilities/image/store.md.

An app hands over an image it already has (a buyer's upload, say) and gets back a name the other image tools take,
such as image.generate's input_images (2026-10-22, for a site's custom-design flow). Uploads are kept with the tool
server's own images under an "upload-" prefix, so the sweep here never touches anything else.
"""
from __future__ import annotations

import base64
import binascii
import secrets
import time

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError

MAX_BYTES = 10 * 1024 * 1024
KEEP_S = 7 * 86400
MAX_UPLOADS = 500  # all apps together, so one app can't fill the disk
PREFIX = "upload-"
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _sweep(now: float) -> int:
    """Delete uploads older than KEEP_S; the number left."""
    left = 0
    for f in prov.output_dir().glob(PREFIX + "*"):
        try:
            if now - f.stat().st_mtime > KEEP_S:
                f.unlink()
            else:
                left += 1
        except OSError:
            pass
    return left


@tool(name="store", category="image", doc="image/store.md")
def store(image_b64: str) -> dict:
    """Keep an image you already have, for 7 days, and get a name the other image tools take.

    Args:
        image_b64: the image (PNG, JPEG or WebP, up to 10 MB), base64. A "data:image/...;base64," start is fine.

    Returns:
        {"name", "mime", "bytes", "expires_at"}: pass "name" to image.generate's input_images (or any tool that
        takes an image name).
    """
    raw = (image_b64 or "").strip()
    if raw.startswith("data:"):
        raw = raw.partition(",")[2]
    if not raw:
        raise ImageError("image_b64 is empty")
    if len(raw) > (MAX_BYTES * 4) // 3 + 4:
        raise ImageError(f"the image is over {MAX_BYTES // 2**20} MB")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ImageError("image_b64 isn't valid base64") from None
    if len(data) > MAX_BYTES:
        raise ImageError(f"the image is over {MAX_BYTES // 2**20} MB")
    mime = prov.sniff_mime(data)
    if mime not in EXT:
        raise ImageError("only PNG, JPEG or WebP images can be stored")
    d = prov.output_dir()
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if _sweep(now) >= MAX_UPLOADS:
        raise ImageError(f"the upload store is full ({MAX_UPLOADS} images kept for 7 days); try again later")
    name = f"{PREFIX}{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}.{EXT[mime]}"
    (d / name).write_bytes(data)
    return {"name": name, "mime": mime, "bytes": len(data),
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + KEEP_S))}
