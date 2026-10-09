"""image.text_check. See ../../capabilities/image/text_check.md."""
from __future__ import annotations

import base64

import httpx

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError, base_url, raise_for_backend
from tools.image.print_file import MAX_BYTES, NAME_RE


@tool(name="text_check", category="image", doc="image/text_check.md")
def text_check(job_id: str = "", image_name: str = "", image_b64: str = "") -> dict:
    """Does a design image carry text: words, pseudo-text or a signature-like mark? Designs carry none unless it's
    added as vector (2026-10-07: a local design came out with a garbled mark in its corner). Free, local, about 1-2 s.

    Args:
        job_id: a finished image.generate job (local or paid). Or:
        image_name: a saved image's name (saved_as). Or:
        image_b64: the image itself (up to 25 MB).

    Returns:
        {"ok", "text_found", "regions": [{"x", "y", "w", "h", "corner": "br"|"bl"|"tr"|"tl"|null, "score",
        "read_as", "text"}], "candidates", "size", "method", "ms"}. "regions" lists every candidate, flagged first;
        "text" marks the ones the rule counts. ok is false when text was found.
    """
    if sum(1 for s in (job_id, image_name, image_b64) if s) != 1:
        raise ImageError("give exactly one of job_id, image_name or image_b64")
    if job_id:
        from tools.image.status import status
        job = status(job_id)
        if job.get("status") != "done" or not job.get("saved_as"):
            raise ImageError(f"job {job_id} isn't finished ({job.get('status')})")
        image_name = job["saved_as"]
    if image_name:
        name = image_name.strip()
        if not NAME_RE.match(name):
            raise ImageError(f"{name!r} isn't an image file name (use the image's saved_as)")
        paid = prov.output_dir() / name  # a paid provider's image is kept on the tool server
        if paid.is_file():
            if paid.stat().st_size > MAX_BYTES:
                raise ImageError(f"{name} is over 25 MB")
            body = {"image_b64": base64.b64encode(paid.read_bytes()).decode()}
        else:
            body = {"name": name}
    else:
        if len(image_b64) > MAX_BYTES * 4 // 3 + 4:
            raise ImageError("image_b64 is over 25 MB")
        body = {"image_b64": image_b64}
    try:
        resp = httpx.post(f"{base_url()}/image/text-check", json=body, timeout=60)
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(resp)
    return resp.json()
