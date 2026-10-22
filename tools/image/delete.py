"""image.delete. See ../../capabilities/image/delete.md.

Delete one image the tool server keeps, now rather than at the 7-day sweep (2026-10-22: a site deletes a buyer's
upload as soon as its design is made). An app deletes only its own: an upload names the app that stored it, and a paid
provider's image has its job's sidecar, which records who asked. The owner token may delete any kept image.
"""
from __future__ import annotations

import json
import re

from registry import tool
from tools.image import providers as prov
from tools.image.generate import ImageError
from tools.image.refs import NAME_REF
from tools.image.store import app_tag, uploader

JOB_RE = re.compile(r"-([0-9a-f]{12})\.(?:png|jpg|jpeg|webp)$")  # a paid job's image ends in -<job_id>.<ext>


def _owner(name: str) -> tuple[str | None, str | None]:
    """(the app that made a kept image, its sidecar's file name), or (None, None) when nothing records it."""
    if who := uploader(name):
        return who, None
    if m := JOB_RE.search(name):
        side = prov.output_dir() / f"{m[1]}.json"
        try:
            job = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None
        if job.get("saved_as") == name:
            return app_tag(job.get("caller")), side.name
    return None, None


@tool(name="delete", category="image", doc="image/delete.md")
def delete(name: str) -> dict:
    """Delete one image the tool server keeps (an image.store upload, or a paid provider's image) straight away.

    Args:
        name: the image's name: image.store's "name", or a paid job's "saved_as".

    Returns:
        {"deleted": name, "sidecar_deleted": bool}. A paid job's .json record goes with its image.
    """
    import clients
    name = (name or "").strip()
    if not NAME_REF.match(name):
        raise ImageError(f"{name[:80]!r} isn't an image name (use image.store's name or a job's saved_as)")
    path = prov.output_dir() / name
    if not path.is_file():
        # "not found" / "not yours" open the message, so a caller can tell these apart from a passing error
        raise ImageError(f"not found: {name} isn't kept here (only image.store uploads and paid providers' images are; "
                         "images on the local image service aren't deleted by this tool)")
    caller = clients.current_client.get()
    who, side = _owner(name)
    if caller is not None and (who != app_tag(caller) or who == app_tag(None)):  # an app called "owner" isn't the owner
        raise ImageError(f"not yours: {name} belongs to another app (an app may delete only the images it stored or "
                         "asked for)")
    path.unlink()
    gone = False
    if side:
        try:
            (prov.output_dir() / side).unlink()
            gone = True
        except OSError:
            pass
    return {"deleted": name, "sidecar_deleted": gone}
