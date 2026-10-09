"""image.status. See ../../capabilities/image/generate.md (same job model for every provider)."""
from __future__ import annotations

import re

import httpx

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError, base_url, raise_for_backend

_JOB_ID = re.compile(r"^[a-f0-9]{12}$")


@tool(name="status", category="image", doc="image/generate.md")
def status(job_id: str, include_image: bool = False) -> dict:
    """Check one image job started by image.generate, local or paid: status running/done/failed, a progress
    text (e.g. "step 7/20"), elapsed_s, the licence, and error if it failed.

    Args:
        job_id: the 12-hex-char id image.generate returned.
        include_image: add the image ("image_b64", "image_mime") when done: a <=1024px JPEG preview for local jobs,
            the file itself (up to 2 MB) for paid ones. Leave False for agents; the dashboard sets it.
    """
    if not isinstance(job_id, str) or not _JOB_ID.match(job_id):
        raise ImageError("job_id must be the 12-character id returned by image.generate")
    remote = prov.remote_status(job_id, include_image)
    if remote is not None:
        return remote
    base = base_url()
    try:
        resp = httpx.get(f"{base}/image/jobs/{job_id}", params={"include_image": include_image},
                         timeout=30)
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    if resp.status_code == 404:
        raise ImageError("unknown job id (the GPU service keeps the last 20 jobs in memory)")
    raise_for_backend(resp)
    out = resp.json()
    if not include_image:
        out.pop("image_b64", None)
        out.pop("image_png_b64", None)
    return out
