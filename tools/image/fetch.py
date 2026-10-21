"""image.fetch. See ../../capabilities/image/fetch.md."""
from __future__ import annotations

import base64
import re

import httpx

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError, base_url, raise_for_backend
from tools.image.print_file import MAX_BYTES

_JOB_ID = re.compile(r"^[a-f0-9]{12}$")


DEFAULT_MB, MAX_MB = 1.0, MAX_BYTES / 1024 / 1024  # base64 lands in the caller's context: small unless asked for


@tool(name="fetch", category="image", doc="image/fetch.md")
def fetch(job_id: str, max_mb: float = DEFAULT_MB) -> dict:
    """Download a finished image job's full-size original, byte for byte (image.status only gives a <=1024 px JPEG
    preview for big images). Paid-provider jobs keep the provider's own format; local jobs are PNG.

    Args:
        job_id: the 12-hex-char id image.generate returned; the job must be done.
        max_mb: the largest file returned (default 1 MB; up to 25). The bytes come back as base64 text (a third
            bigger), so a big file fills the caller's context: ask for more only to save the file, not to look at it.

    Returns:
        {"job_id", "name", "image_b64", "image_mime", "bytes"}. A file over max_mb is refused with its size, so the
        caller can ask again with a higher max_mb.
    """
    if not isinstance(job_id, str) or not _JOB_ID.match(job_id):
        raise ImageError("job_id must be the 12-character id returned by image.generate")
    if isinstance(max_mb, bool) or not isinstance(max_mb, (int, float)) or not 0 < max_mb <= MAX_MB:
        raise ImageError(f"max_mb is a number above 0, up to {MAX_MB:g}")
    limit = int(max_mb * 1024 * 1024)
    remote = prov.remote_status(job_id)
    if remote is not None:
        name = _done_name(remote)
        path = prov.output_dir() / name
        if not path.is_file():
            raise ImageError("the job's image file is no longer on the tool server")
        _check_size(path.stat().st_size, limit)
        data = path.read_bytes()
    else:
        base = base_url()
        try:
            resp = httpx.get(f"{base}/image/jobs/{job_id}", timeout=30)
            if resp.status_code == 404:
                raise ImageError("unknown job id (the GPU service keeps the last 20 jobs in memory)")
            raise_for_backend(resp)
            name = _done_name(resp.json())
            resp = httpx.get(f"{base}/image/files/{name}", timeout=120)
        except httpx.HTTPError as exc:
            raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
        if resp.status_code == 404:
            raise ImageError("the job's image file is no longer on the image service")
        raise_for_backend(resp)
        data = resp.content
        _check_size(len(data), limit)
    mime = prov.sniff_mime(data) or "application/octet-stream"
    return {"job_id": job_id, "name": name, "image_b64": base64.b64encode(data).decode(), "image_mime": mime,
            "bytes": len(data)}


def _check_size(size: int, limit: int) -> None:
    if size > MAX_BYTES:
        raise ImageError("that image is over 25 MB (take it from the image service's folder instead)")
    if size > limit:
        mb = size / 1024 / 1024
        hint = f"; ask again with max_mb={min(MAX_MB, mb + 0.1):.1f}" if size <= MAX_BYTES else " (the limit is 25 MB)"
        raise ImageError(f"that image is {mb:.1f} MB, over max_mb{hint}")


def _done_name(job: dict) -> str:
    if job.get("status") != "done" or not job.get("saved_as"):
        raise ImageError(f"the job is {job.get('status', 'not finished')}; fetch works once it is done")
    return job["saved_as"]
