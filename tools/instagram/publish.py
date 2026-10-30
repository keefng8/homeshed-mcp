"""instagram.publish and instagram.status. See ../../capabilities/instagram/publish.md."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import httpx

from paths import data_path
from registry import tool
from tools.commerce import _http as h
from tools.instagram import _client as ig

API = ig.API
MAX_CHARS, MAX_HASHTAGS, MAX_IMAGES = 2200, 5, 10  # caption, hashtags (Instagram allows 5 since Dec 2025, R&D's
# review researchandimprovements/instagram-posts-review.md; 30 before) and carousel limits
MAX_IMAGE_BYTES, MIN_W, MAX_W = 8 * 2**20, 320, 1440
MIN_RATIO, MAX_RATIO = 0.8, 1.91  # feed images: 4:5 portrait to 1.91:1 landscape (Meta crops nothing; it refuses)
LOCAL_PX = 1440
POSTS_FILE = Path(os.environ.get("INSTAGRAM_POSTS_FILE") or data_path("usage/instagram_posts.json"))
WAIT_S, WAIT_STEP_S = 60, 3  # Meta fetches the image while the container processes
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(png|jpg|jpeg)$")
AI_LINE = "Image made with AI."
_lock = threading.Lock()


def _daily_max() -> int:
    try:
        return max(0, min(100, int(os.environ.get("INSTAGRAM_DAILY_MAX") or 25)))  # Meta allows 100 API posts a day
    except ValueError:
        return 25


def _recent() -> list[dict]:
    try:
        rows = json.loads(POSTS_FILE.read_text(encoding="utf-8")).get("posts") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    return [r for r in rows if isinstance(r, dict) and time.time() - float(r.get("at") or 0) < 86400]


def _record(row: dict) -> None:
    rows = _recent() + [row]
    POSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = POSTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"posts": rows[-300:]}, indent=1), encoding="utf-8")
    os.replace(tmp, POSTS_FILE)


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG header (no image library in the tool server)."""
    i = 2
    while data[:2] == b"\xff\xd8" and i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker, seg = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + seg
    return None


def _media(name: str) -> bytes:
    """A JPEG for one image by its saved_as name, checked against Meta's limits before anything is uploaded. Local
    images come as the image service's JPEG (up to 1440 px); a paid one is used as it is, so it must be a JPEG."""
    from tools.image import providers as prov
    if not NAME_RE.match(name or ""):
        raise h.CommerceError(f"{name!r} isn't an image file name (use the image's saved_as)")
    paid = prov.output_dir() / name
    if paid.is_file():
        data = paid.read_bytes()
    else:
        from tools._helper_auth import helper_headers
        from tools.image.generate import base_url
        try:
            r = httpx.get(f"{base_url()}/image/files/{name}/thumb", params={"px": LOCAL_PX}, timeout=60,
                          headers=helper_headers())
        except httpx.HTTPError as exc:
            raise h.CommerceError(f"the image service didn't answer ({type(exc).__name__})") from None
        if r.status_code == 404:
            raise h.CommerceError(f"no image called {name}")
        if r.status_code != 200:
            raise h.CommerceError(f"the image service couldn't give {name} (HTTP {r.status_code})")
        data = r.content
    if prov.sniff_mime(data) != "image/jpeg":
        raise h.CommerceError(f"{name} isn't a JPEG (Instagram takes only JPEG): make it with image.carousel first")
    if len(data) > MAX_IMAGE_BYTES:
        raise h.CommerceError(f"{name} is over 8 MB (Instagram's limit)")
    size = _jpeg_size(data)
    if not size or not MIN_W <= size[0] <= MAX_W:
        raise h.CommerceError(f"{name} is {size[0] if size else '?'} px wide; Instagram takes {MIN_W}-{MAX_W}")
    ratio = size[0] / max(1, size[1])
    if not MIN_RATIO - 0.005 <= ratio <= MAX_RATIO + 0.005:
        raise h.CommerceError(f"{name} is {size[0]}x{size[1]}; Instagram's feed takes 4:5 (portrait) to 1.91:1: "
                              'use image.carousel shape "4:5" or "1:1"')
    return data


def _create(uid: str, tok: str, body: dict) -> str:
    cid = h.json_of("instagram", h.request("instagram", "POST", f"{API}/{uid}/media", headers={}, secrets=(tok,),
                                           data={**body, "access_token": tok})).get("id")
    if not cid:
        raise h.CommerceError("Instagram didn't create the post")
    return str(cid)


def _wait(cid: str, tok: str) -> None:
    waited = 0
    while waited < WAIT_S:
        st = h.json_of("instagram", h.request("instagram", "GET", f"{API}/{cid}", headers={}, secrets=(tok,),
                                              params={"fields": "status_code,status", "access_token": tok}))
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise h.CommerceError(f"Instagram couldn't prepare the post: {h.clean(st.get('status') or code, (tok,))}")
        time.sleep(WAIT_STEP_S)
        waited += max(WAIT_STEP_S, 1)
    raise h.CommerceError("Instagram was still preparing the post after a minute: nothing was posted, try again")


@tool(name="publish", category="instagram", doc="instagram/publish.md")
def publish(text: str, ai_generated: bool, image_names: list[str], alt_text: str = "") -> dict:
    """Post to the owner's connected Instagram professional account: one image, or a carousel of 2-10. PUBLIC, and
    can't be unposted from here: only call it for a post that has been approved for publishing. Off until the owner
    switches "Let apps post to Instagram" on.

    Args:
        text: the caption, 1-2,200 characters and at most 5 #hashtags (with the AI line when ai_generated). Links in
            a caption aren't clickable on Instagram: say "link in bio".
        ai_generated: required. True when any of it was made by AI: a last line "Image made with AI." is added.
        image_names: 1-10 images by their saved_as name: 1 = an image post, 2-10 = a carousel. JPEG, 320-1440 px wide,
            4:5 to 1.91:1 (image.carousel's "4:5" or "1:1" slides are made for this). Staged privately for about an
            hour and deleted after posting.
        alt_text: a description read out for each image (screen readers); up to 1000 characters.

    Returns:
        {"posted": true, "id", "permalink", "media_type", "posted_today", "daily_max", "ai_generated", "ai_line"}.
    """
    h.require(ig.SETTING, "Instagram posting")
    if not isinstance(ai_generated, bool):
        raise h.CommerceError("ai_generated is required: true when any of the post was made by AI, else false")
    text = (text or "").strip()
    if not text:
        raise h.CommerceError("the caption is empty")
    added = False
    if ai_generated and "made with ai" not in text.lower():
        text, added = f"{text}\n\n{AI_LINE}", True
    if len(text) > MAX_CHARS:
        raise h.CommerceError(f"the caption is over {MAX_CHARS} characters by {len(text) - MAX_CHARS}"
                              + ("; that includes the AI line" if added else ""))
    if len(re.findall(r"(?<!\w)#\w", text)) > MAX_HASHTAGS:
        raise h.CommerceError(f"Instagram allows at most {MAX_HASHTAGS} #hashtags in a caption")
    names = [str(n).strip() for n in (image_names or []) if str(n).strip()]
    if not 1 <= len(names) <= MAX_IMAGES:
        raise h.CommerceError(f"give 1-{MAX_IMAGES} images (Instagram has no text-only posts)")
    alt = {"alt_text": " ".join(alt_text.split())[:1000]} if (alt_text or "").strip() else {}
    media = [_media(n) for n in names]  # every image checked before anything is uploaded or posted
    import clients
    import r2
    who = clients.current_client.get() or "owner"
    staged: list[str] = []
    with _lock:
        today = len(_recent())
        if today >= _daily_max():
            raise h.CommerceError(f"{today} posts in the last 24 hours: the daily limit ({_daily_max()}) is reached")
        tok, uid = ig.token()
        try:
            urls = []
            for data in media:
                key = f"instagram/{time.strftime('%Y%m%d')}/{uuid.uuid4().hex}.jpg"
                r2.put(key, data, "image/jpeg")
                staged.append(key)
                urls.append(r2.presign_get(key, 3600))
            if len(urls) == 1:
                cid, kind = _create(uid, tok, {"image_url": urls[0], "caption": text, **alt}), "IMAGE"
            else:
                kids = [_create(uid, tok, {"image_url": u, "is_carousel_item": "true", **alt}) for u in urls]
                for k in kids:
                    _wait(k, tok)
                cid, kind = _create(uid, tok, {"media_type": "CAROUSEL", "children": ",".join(kids),
                                               "caption": text}), "CAROUSEL"
            _wait(cid, tok)
            pid = h.json_of("instagram", h.request("instagram", "POST", f"{API}/{uid}/media_publish", headers={},
                                                   secrets=(tok,), data={"creation_id": cid, "access_token": tok})).get("id")
            if not pid:
                raise h.CommerceError("Instagram didn't publish the post")
            _record({"at": time.time(), "id": str(pid), "by": who, "media_type": kind, "ai_generated": ai_generated})
        finally:
            for key in staged:  # Meta fetched them while preparing; nothing stays public-by-link
                r2.delete(key)
    link, gone = None, False
    for attempt in range(3):  # read it back: on 2026-10-20 Meta gave an id for a post it then didn't keep
        try:
            link = h.json_of("instagram", h.request("instagram", "GET", f"{API}/{pid}", headers={}, secrets=(tok,),
                                                    params={"fields": "permalink", "access_token": tok})).get("permalink")
            break
        except h.CommerceError as exc:
            gone = "does not exist" in str(exc) or "Unsupported get request" in str(exc)
            if attempt < 2:
                time.sleep(3)
    clients._audit("instagram post", None if who == "owner" else who, post=str(pid), ai_generated=ai_generated,
                   **({"not_visible": True} if gone and not link else {}))
    out = {"posted": True, "id": str(pid), "permalink": link, "media_type": kind, "posted_today": today + 1,
           "daily_max": _daily_max(), "ai_generated": ai_generated, "ai_line": AI_LINE if added else None}
    if gone and not link:
        out.update(posted=False, warning="Instagram returned an id but doesn't show the post (it may have removed it, "
                                         "e.g. posts too close together): check the account, and space posts out")
    return out


@tool(name="status", category="instagram", doc="instagram/status.md")
def status() -> dict:
    """Is Instagram connected and is posting switched on? Account name, posts in the last 24 hours and the daily
    limit. Never shows a token."""
    st = ig.status()
    return {"connected": st["connected"], "enabled": st["enabled"], "account": st["account"],
            "app_ready": st["app_ready"], "posted_today": len(_recent()), "daily_max": _daily_max()}
