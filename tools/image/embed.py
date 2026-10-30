"""image.embed. See ../../capabilities/image/embed.md."""
from __future__ import annotations

import base64

import httpx

from registry import tool
from tools._helper_auth import helper_headers
from tools.image.generate import ImageError, base_url, raise_for_backend

MAX_BYTES = 20 * 1024 * 1024
MAX_B64_BYTES = 10 * 1024 * 1024  # an image sent inline (decoded): a design is a few MB at most


@tool(name="embed", category="image", doc="image/embed.md")
def embed(image: str = "", image_b64: str = "") -> dict:
    """An image's embedding: 768 numbers from SigLIP 2 (google/siglip2-base-patch16-224, Apache-2.0) that place similar
    pictures close together, for a classifier trained on the owner's ratings (a shop's shadow design judge). Free, on
    the image service's CPU only, never its GPU: about 1 s, or ~10 s when the model has to load first.

    Args:
        image: an image name (saved_as), "folder/name.png", or a Printify mockup address. A print file's see-through
            parts count as white.
        image_b64: or the image itself, base64 (up to 10 MB decoded), for one that isn't in the image store (2026-10-09:
            21 of the owner's 61 rated designs were uploaded as data). Give image or image_b64, not both.

    Returns:
        {"model", "dim", "vector" (L2-normalised floats), "ms"}. Keep the model name with each vector: vectors from
        another model aren't comparable.
    """
    if bool(image) == bool(image_b64):
        raise ImageError("give image (a name) or image_b64 (the image itself), one of them")
    if image_b64:
        if len(image_b64) > MAX_B64_BYTES * 4 // 3 + 4:
            raise ImageError("image_b64 is at most 10 MB")
        try:
            data = base64.b64decode(image_b64, validate=True)
        except ValueError:
            raise ImageError("image_b64 isn't base64") from None
        if not data:
            raise ImageError("image_b64 is empty")
    else:
        from tools.commerce._http import CommerceError
        from tools.image.refs import image_bytes
        try:
            data = image_bytes(image, MAX_BYTES)
        except CommerceError as exc:
            raise ImageError(str(exc)) from None
    try:
        resp = httpx.post(f"{base_url()}/image/embed", json={"image_b64": base64.b64encode(data).decode()}, timeout=60,
                          headers=helper_headers())
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(resp)
    return resp.json()
