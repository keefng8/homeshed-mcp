"""image.store. See ../../capabilities/image/store.md.

An app hands over an image it already has (a buyer's upload, say) and gets back a name the other image tools take,
such as image.generate's input_images (2026-10-22, for a site's custom-design flow). Uploads are kept with the tool
server's own images under an "upload-<app>-" prefix: the sweep here never touches anything else, and image.delete
knows whose each one is.

Every upload is re-encoded (2026-10-22, R&D's privacy review): EXIF, GPS and any other metadata are dropped before the
photo goes anywhere, and an oversized or decompression-bomb image is refused before it's decoded.
"""
from __future__ import annotations

import base64
import binascii
import io
import re
import secrets
import time
import warnings

from PIL import Image, ImageOps

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_SIDE = 4096
KEEP_S = 7 * 86400
MAX_UPLOADS = 500  # all apps together, so one app can't fill the disk
PREFIX = "upload-"
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
FORMAT = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
# upload-<app>-<YYYYmmdd-HHMMSS>-<8 hex>.<ext>: the app is everything between the prefix and the time
UPLOAD_RE = re.compile(r"^upload-([a-z0-9][a-z0-9-]*)-(\d{8}-\d{6})-[0-9a-f]{8}\.(?:png|jpg|webp)$")


def uploader(name: str) -> str | None:
    """The app that stored an upload, or None (not an upload, or one from before names carried it)."""
    m = UPLOAD_RE.match(name)
    return m[1] if m else None


def app_tag(client: str | None) -> str:
    """An app's name as it appears in its uploads' names."""
    return re.sub(r"[^a-z0-9-]+", "-", (client or "owner").lower()).strip("-") or "owner"


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


def _clean(data: bytes, mime: str) -> tuple[bytes, tuple[int, int]]:
    """The image re-encoded with no metadata (turned upright first, so the EXIF orientation can go too), and its size."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            im = Image.open(io.BytesIO(data))
            if im.format != FORMAT[mime]:
                raise ImageError("the image's contents don't match its type")
            if max(im.size) > MAX_SIDE:  # read from the header, before anything is decoded
                raise ImageError(f"the image is {im.size[0]}x{im.size[1]} px; at most {MAX_SIDE} px a side")
            im = ImageOps.exif_transpose(im)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageError("the image decodes far larger than its file (a decompression bomb)") from None
    except (OSError, SyntaxError, ValueError):
        raise ImageError("the image couldn't be read (damaged or not really an image)") from None
    if mime == "image/jpeg" and im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        im = im.convert("RGBA")
    out = io.BytesIO()
    im.save(out, FORMAT[mime], **({"quality": 92} if mime != "image/png" else {}))
    return out.getvalue(), im.size


@tool(name="store", category="image", doc="image/store.md")
def store(image_b64: str) -> dict:
    """Keep an image you already have, for 7 days, and get a name the other image tools take. It's re-encoded on the
    way in: EXIF, GPS and other metadata are removed.

    Args:
        image_b64: the image (PNG, JPEG or WebP, up to 10 MB and 4096 px a side), base64. A "data:image/...;base64,"
            start is fine.

    Returns:
        {"name", "mime", "bytes", "width", "height", "expires_at"}: pass "name" to image.generate's input_images (or
        any tool that takes an image name), and to image.delete when you're done with it.
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
    data, (width, height) = _clean(data, mime)
    d = prov.output_dir()
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if _sweep(now) >= MAX_UPLOADS:
        raise ImageError(f"the upload store is full ({MAX_UPLOADS} images kept for 7 days); try again later")
    import clients
    name = (f"{PREFIX}{app_tag(clients.current_client.get())}-{time.strftime('%Y%m%d-%H%M%S')}-"
            f"{secrets.token_hex(4)}.{EXT[mime]}")
    (d / name).write_bytes(data)
    return {"name": name, "mime": mime, "bytes": len(data), "width": width, "height": height,
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + KEEP_S))}
