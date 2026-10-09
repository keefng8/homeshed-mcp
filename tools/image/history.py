"""image.history. See ../../capabilities/image/history.md."""
from __future__ import annotations

import json

import httpx

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError, base_url

MAX_LIMIT = 100
LOCAL_KEYS = ("prompt", "original_prompt", "model", "seed", "width", "height")
PAID_KEYS = ("prompt", "provider", "model", "seed", "width", "height", "license")


def _local(limit: int) -> tuple[list[dict], str | None]:
    """The local image service's recent images with their records, or ([], why) when it can't be read."""
    try:
        base = base_url()
        resp = httpx.get(f"{base}/image/files", params={"limit": limit}, timeout=15)
        files = resp.json().get("files") if resp.status_code == 200 else None
    except (ImageError, httpx.HTTPError, ValueError, AttributeError):
        files = None
    if not isinstance(files, list):
        return [], "the local image service didn't answer: only paid-provider images are listed"
    rows = [{"name": f.get("name"), "source": "local", "at": f.get("mtime"),
             **{k: f[k] for k in LOCAL_KEYS if f.get(k) is not None}} for f in files if isinstance(f, dict)]
    return rows, None


def _paid(limit: int) -> list[dict]:
    """Finished paid-provider images, from the .json records saved with them."""
    folder = prov.output_dir()
    if not folder.is_dir():
        return []
    rows = []
    for f in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[: limit * 2]:
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(job, dict) or job.get("status") != "done" or not job.get("saved_as"):
            continue
        rows.append({"name": job["saved_as"], "source": "paid", "at": job.get("finished_at"),
                     **{k: job[k] for k in PAID_KEYS if job.get(k) is not None}})
    return rows


@tool(name="history", category="image", doc="image/history.md")
def history(limit: int = 20) -> dict:
    """Recent generated images with the prompt each was made with (and the original wording before enhancement,
    when it was enhanced), model, seed and size, newest first: local images and paid-provider ones together. Use it
    to see which prompts worked, or to reuse one. Read-only; never returns image data (image.status does).

    Args:
        limit: how many images, 1-100 (default 20).

    Returns:
        {"images": [{"name", "source": "local" | "paid", "at", "prompt"?, "original_prompt"?, "model"?, "seed"?,
        "width"?, "height"?, "provider"?, "license"?}], "count", "note"?}. Images made before records were kept
        (2026-10-06) have no prompt.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ImageError(f"limit must be a whole number from 1 to {MAX_LIMIT}")
    local, note = _local(limit)
    rows = sorted(local + _paid(limit), key=lambda r: -(float(r.get("at") or 0)))[:limit]
    return {"images": rows, "count": len(rows), **({"note": note} if note else {})}
