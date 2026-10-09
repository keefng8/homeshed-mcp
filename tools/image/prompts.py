"""image.prompts. See ../../capabilities/image/prompts.md."""
from __future__ import annotations

import httpx

from registry import tool
from tools.image.generate import ImageError, base_url, raise_for_backend

ACTIONS = ("list", "add", "edit", "remove")


@tool(name="prompts", category="image", doc="image/prompts.md")
def prompts(action: str = "list", prompt_id: str = "", prompt: str = "", name: str = "", size: str = "1024x1024",
            upscale: bool = False, folder: str = "") -> dict:
    """The saved image prompts the owner keeps on the Control Panel's Images page: list, add, edit or remove one.
    To make an image from one, pass its prompt (and size, upscale, folder) to image.generate.

    Args:
        action: list | add | edit | remove.
        prompt_id: the prompt's id, for edit and remove.
        prompt: the text (1-2000 chars), for add and edit.
        name: a short label (up to 80 chars); empty uses the start of the prompt.
        size: 512x512 | 768x768 | 1024x1024 | 1024x576 | 576x1024.
        upscale: 4x after generation.
        folder: the images folder its images go into (empty: the main folder).

    Returns:
        list: {"items": [{"id", "name", "prompt", "size", "upscale", "folder"}], "most"}; add/edit: the prompt;
        remove: {"deleted": id}.
    """
    if action not in ACTIONS:
        raise ImageError(f"action must be one of {', '.join(ACTIONS)}")
    if action in ("edit", "remove") and not prompt_id.strip():
        raise ImageError(f"{action} needs prompt_id (image.prompts with action=list shows them)")
    base = base_url()
    body = {"prompt": prompt, "name": name, "size": size, "upscale": upscale, "folder": folder.strip() or None}
    url = f"{base}/image/prompts" + (f"/{prompt_id.strip()}" if action in ("edit", "remove") else "")
    method = {"list": "GET", "add": "POST", "edit": "PUT", "remove": "DELETE"}[action]
    try:
        resp = httpx.request(method, url, json=body if action in ("add", "edit") else None, timeout=10)
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(resp)
    return resp.json()
