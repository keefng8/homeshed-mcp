"""image.cancel. See ../../capabilities/image/cancel.md.

2026-10-08: a shop app's dropped training batch left 4 stand-in jobs on the GPU for half an hour, and there was no way for
an app to stop its own jobs. A waiting local job leaves the line; a running one is stopped. An app may cancel only the
jobs it asked for (the image service records who asked); the owner may cancel any.
"""
from __future__ import annotations

import re

import httpx

from registry import tool
from tools.image import providers as prov
from tools._helper_auth import helper_headers
from tools.image.generate import ImageError, _caller, base_url

DONE = ("done", "failed", "cancelled")


@tool(name="cancel", category="image", doc="image/cancel.md")
def cancel(job_id: str) -> dict:
    """Stop one of your image jobs: a waiting one leaves the line, a running local one is stopped (nothing is saved).
    An app can cancel only jobs it asked for; the owner can cancel any. A paid provider's job can't be stopped once
    sent (it finishes, and its cost stands).

    Args:
        job_id: the 12-character id image.generate returned.

    Returns:
        {"ok", "job_id", "status": "cancelled" | "already done" | "not found" | "running", "was"?, "note"?}. ok is
        true when the job is no longer running or waiting.
    """
    if not re.fullmatch(r"[a-f0-9]{12}", job_id or ""):
        raise ImageError("job_id is the 12-character id image.generate returned")
    me = _caller()
    try:
        r = httpx.get(f"{base_url()}/image/jobs/{job_id}", timeout=15, headers=helper_headers())
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    if r.status_code == 404:  # not a local job: perhaps a paid provider's
        remote = prov.remote_status(job_id)
        if remote is None:
            return {"ok": False, "job_id": job_id, "status": "not found"}
        if remote.get("status") in DONE:
            return {"ok": True, "job_id": job_id, "status": "already done", "was": remote.get("status")}
        return {"ok": False, "job_id": job_id, "status": "running",
                "note": "a paid provider's job can't be stopped once sent; it finishes, and its cost stands"}
    if r.status_code != 200:
        raise ImageError(f"image backend answered {r.status_code}")
    job = r.json()
    if me != "owner" and job.get("requested_by") != me:
        raise ImageError("that job wasn't started by this app: an app can cancel only its own jobs")
    if job.get("status") in DONE:
        return {"ok": True, "job_id": job_id, "status": "already done", "was": job.get("status")}
    try:
        c = httpx.post(f"{base_url()}/image/jobs/{job_id}/cancel", timeout=15, headers=helper_headers())
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    if c.status_code == 409:  # it finished between the two calls
        return {"ok": True, "job_id": job_id, "status": "already done"}
    if c.status_code != 200:
        raise ImageError(f"image backend answered {c.status_code} to the cancel")
    import clients
    clients._audit("image cancel", None if me == "owner" else me, job=job_id, was=job.get("status"))
    return {"ok": True, "job_id": job_id, "status": "cancelled", "was": job.get("status")}
