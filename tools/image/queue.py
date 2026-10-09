"""image.queue. See ../../capabilities/image/queue.md."""
from __future__ import annotations

import httpx

from registry import tool
from tools.image.generate import ImageError, base_url, raise_for_backend

_SHOWN = ("job_id", "status", "queue_position", "progress", "prompt", "original_prompt", "folder", "width", "height",
          "elapsed_s")


@tool(name="queue", category="image", doc="image/queue.md")
def queue() -> dict:
    """The local image service's line: whether it's busy, the image being made and the ones waiting, in order.
    Check this before queuing a batch: image.generate never blocks, but the line holds at most 20.

    Returns:
        {"busy", "running": job | None, "waiting": [job, ...], "waiting_count", "room"}; each job is {"job_id",
        "status", "queue_position", "progress", "prompt", "original_prompt", "folder", "width", "height", "elapsed_s"}.
    """
    base = base_url()
    try:
        jobs = httpx.get(f"{base}/image/jobs", timeout=10)
        models = httpx.get(f"{base}/image/models", timeout=10)
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(jobs)
    raise_for_backend(models)
    rows = [{k: j.get(k) for k in _SHOWN} for j in jobs.json().get("jobs") or []]
    running = next((j for j in rows if j["status"] == "running"), None)
    waiting = sorted((j for j in rows if j["status"] == "queued"), key=lambda j: j.get("queue_position") or 0)
    info = models.json()
    most = int(info.get("max_queued") or 0)
    return {"busy": bool(info.get("busy")), "running": running, "waiting": waiting, "waiting_count": len(waiting),
            "room": max(0, most - len(waiting)) if most else None}
