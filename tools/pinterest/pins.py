"""pinterest.status, pinterest.boards.list, pinterest.board.create and pinterest.pin.create. See
../../capabilities/pinterest/pins.md.

Pinterest's developer guidelines: the person chooses each pin. So a pin (and a board) is only ever ASKED for here: it is
checked, then waits in the Control Panel's approvals bar until the owner approves that one (printify_pending.py,
actions "pinterest_pin" and "pinterest_board"). No app's own approval or AUTO mode can approve one.

Every pin: one image, a title, a description, alt text, and a link to one of the shop's own active Etsy listings. When
AI helped make it, the description ends "AI-assisted design." and the pin carries Pinterest's own AI disclosure
(ai_disclosures: AI_MODIFIED, API v5 5.28.0). The same image with the same link isn't pinned twice within 30 days (a
spam signal, R&D).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from paths import data_path
from registry import tool
from tools.commerce import _http as h
from tools.pinterest import _client as pc

PINS_FILE = Path(os.environ.get("PINTEREST_PINS_FILE") or data_path("usage/pinterest_pins.json"))
LABEL = "Pinterest pins"
MAX_TITLE, MAX_DESCRIPTION, MAX_ALT, MAX_BOARD_NAME, MAX_BOARD_DESC = 100, 800, 500, 100, 500  # Pinterest's, then ours
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_MIMES = ("image/jpeg", "image/png")
AI_LINE = "AI-assisted design."
REPEAT_S = 30 * 86400
LISTING_GAP_S = 7 * 86400  # one pin per listing a week (a terms check, 2026-10-10)
LISTING_RE = re.compile(r"^https://(?:www\.)?etsy\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?listing/(\d{6,12})(?:[/?#].*)?$", re.I)
_lock = threading.Lock()


def daily_max() -> int:
    try:
        return max(0, min(50, int(os.environ.get("PINTEREST_DAILY_MAX") or 5)))
    except ValueError:
        return 5


def _made() -> list[dict]:
    try:
        rows = json.loads(PINS_FILE.read_text(encoding="utf-8")).get("pins") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def _record(row: dict) -> None:
    with _lock:
        rows = [r for r in _made() if time.time() - float(r.get("at") or 0) < REPEAT_S] + [row]
        PINS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PINS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"pins": rows[-1000:]}, indent=1), encoding="utf-8")
        os.replace(tmp, PINS_FILE)


def _boards() -> list[dict]:
    out, bookmark = [], None
    for _ in range(5):  # up to 500 boards
        d = pc.call("GET", "/v5/boards", params={"page_size": 100, **({"bookmark": bookmark} if bookmark else {})})
        out += [b for b in d.get("items") or [] if isinstance(b, dict)]
        bookmark = d.get("bookmark")
        if not bookmark:
            break
    return out


def _board(board: str) -> dict:
    want = " ".join(str(board or "").split())
    if not want:
        raise h.CommerceError("board: the board's id or its exact name (pinterest.boards.list)")
    rows = _boards()
    hit = next((b for b in rows if str(b.get("id")) == want), None) or \
        next((b for b in rows if str(b.get("name") or "").strip().lower() == want.lower()), None)
    if hit is None:
        raise h.CommerceError(h.trim(f'no board "{want}" on the account; it has: '
                                     + (", ".join(str(b.get("name")) for b in rows) or "none yet (pinterest.board.create)"), 500))
    return hit


def _listing(link: str) -> tuple[str, str]:
    """(canonical link, title) for one of the shop's own ACTIVE Etsy listings, or why not."""
    m = LISTING_RE.match((link or "").strip())
    if not m:
        raise h.CommerceError("link must be one of your Etsy listings: https://www.etsy.com/listing/<number>")
    lid = m.group(1)
    from tools.etsy import _client as et
    h.require(et.SETTING, "Etsy")  # the link is checked against the shop itself
    x = et.get(f"/v3/application/listings/{lid}") or {}
    if not isinstance(x, dict) or str(x.get("shop_id")) != et.shop_id():
        raise h.CommerceError("that listing isn't in your Etsy shop: a pin links only to your own listings")
    if x.get("state") != "active":
        raise h.CommerceError(f"that listing is {x.get('state') or 'not active'} on Etsy: pin it once it's on sale")
    return f"https://www.etsy.com/listing/{lid}", h.trim(x.get("title"), 60) or lid


def _image(ref: str) -> bytes:
    from tools.image.refs import image_bytes
    return image_bytes(ref, MAX_IMAGE_BYTES)


def _text(value, cap: int, what: str, required: bool = True) -> str:
    text = " ".join(str(value or "").split()) if what != "description" else str(value or "").strip()
    if required and not text:
        raise h.CommerceError(f"{what} is needed")
    if len(text) > cap:
        raise h.CommerceError(f"{what}: at most {cap} characters ({len(text)} given)")
    return text


def with_ai_line(description: str) -> str:
    """The description with its AI line (R&D: every AI pin says it), unless it says so already."""
    if "ai-assisted" in description.lower() or "made with ai" in description.lower():
        return description
    return f"{description}\n\n{AI_LINE}" if description else AI_LINE


@tool(name="status", category="pinterest", doc="pinterest/pins.md")
def status() -> dict:
    """Is Pinterest connected, is pinning switched on, and is it Trial access (sandbox: pins only the owner sees) or
    Standard? Account name, pins approved in the last 24 hours and the daily limit. Never shows a token."""
    st = pc.status()
    account = None
    if st["connected"]:
        try:
            account = h.trim(pc.call("GET", "/v5/user_account").get("username"), 60)
        except h.CommerceError as exc:
            account = f"(couldn't read: {h.trim(str(exc), 120)})"
    import printify_pending
    return {"connected": st["connected"], "enabled": st["enabled"], "sandbox": st["sandbox"], "account": account,
            "app_ready": st["app_ready"], "pinned_today": len(printify_pending.done_today("pinterest_pin")),
            "daily_max": daily_max(),
            "note": "Trial access: pins go to Pinterest's sandbox and only the owner sees them" if st["sandbox"] else
                    "Standard access: pins are public"}


@tool(name="boards.list", category="pinterest", doc="pinterest/pins.md")
def boards_list() -> dict:
    """The connected Pinterest account's boards: id, name, privacy, pin count. Read-only."""
    h.require(pc.SETTING, LABEL)
    rows = [{"id": str(b.get("id")), "name": h.trim(b.get("name"), 100), "privacy": b.get("privacy"),
             "pins": b.get("pin_count")} for b in _boards()]
    return {"count": len(rows), "boards": rows, "sandbox": pc.sandbox()}


def _queue(key: str, summary: str, checks: dict, action: str, payload: dict) -> dict:
    import printify_pending
    try:
        return printify_pending.add(key, h.trim(summary, 300), checks, action=action, payload=payload)
    except printify_pending.PendingError as exc:
        raise h.CommerceError(str(exc)) from None


@tool(name="board.create", category="pinterest", doc="pinterest/pins.md")
def board_create(name: str, description: str = "", privacy: str = "PUBLIC") -> dict:
    """Ask to make a Pinterest board (e.g. one per breed, "Dog lover gifts UK"). It is NOT made by this call: it
    waits for the owner's approval in the Control Panel. Off until the owner switches "Let apps ask to pin to
    Pinterest" on.

    Args:
        name: the board's name, up to 100 characters; one the account has already is refused.
        description: up to 500 characters.
        privacy: PUBLIC (default) or SECRET.

    Returns:
        {"pending": true, "id", "pinterest_board": summary, "expires_in_h", "how"}.
    """
    h.require(pc.SETTING, LABEL)
    name = _text(name, MAX_BOARD_NAME, "name")
    description = _text(description, MAX_BOARD_DESC, "board description", required=False)
    privacy = str(privacy or "PUBLIC").upper()
    if privacy not in ("PUBLIC", "SECRET"):
        raise h.CommerceError("privacy: PUBLIC or SECRET")
    if any(str(b.get("name") or "").strip().lower() == name.lower() for b in _boards()):
        raise h.CommerceError(f'the account already has a board "{name}"')
    summary = f'Make the Pinterest board "{name}" ({privacy.lower()})' + (" in the sandbox" if pc.sandbox() else "")
    return _queue(f"pinterest-board-{hashlib.sha256(name.lower().encode()).hexdigest()[:12]}", summary,
                  {"name": name, "privacy": privacy, "sandbox": pc.sandbox()}, "pinterest_board",
                  {"name": name, "description": description, "privacy": privacy, "sandbox": pc.sandbox(),
                   **({"detail": f"BOARD\n{name}\n\nDESCRIPTION\n{description}"} if description else {})})


@tool(name="pin.create", category="pinterest", doc="pinterest/pins.md")
def pin_create(image: str, title: str, description: str, link: str, board: str, ai_generated: bool,
               alt_text: str = "") -> dict:
    """Ask to make one Pinterest pin. It is NOT made by this call: it's checked, then waits for the owner's approval
    of THIS pin in the Control Panel (the image shows there). Pinterest's rule: the person chooses each pin, so no
    app's approval or AUTO mode can approve one. Off until the owner switches "Let apps ask to pin to Pinterest" on.

    Args:
        image: the picture: "folder/name.png" in the image store, an image name, or a Printify mockup address. JPEG
            or PNG up to 10 MB; 2:3 portrait (1000x1500) is what Pinterest recommends.
        title: up to 100 characters, with the words people search for.
        description: up to 800 characters, including the AI line when ai_generated.
        link: one of the shop's own ACTIVE Etsy listings (https://www.etsy.com/listing/<number>).
        board: the board's id or exact name (pinterest.boards.list).
        ai_generated: required. True when AI made or changed any of the image or words: the description gets
            "AI-assisted design." and the pin carries Pinterest's AI disclosure.
        alt_text: what the image shows, for people who can't see it; up to 500 characters.

    Returns:
        {"pending": true, "id", "pinterest_pin": summary, "expires_in_h", "how"}.
    """
    h.require(pc.SETTING, LABEL)
    if not isinstance(ai_generated, bool):
        raise h.CommerceError("ai_generated is required: true when AI made or changed any of it, else false")
    title = _text(title, MAX_TITLE, "title")
    description = _text(description, MAX_DESCRIPTION, "description")
    if ai_generated:
        description = with_ai_line(description)
        if len(description) > MAX_DESCRIPTION:
            raise h.CommerceError(f"description: at most {MAX_DESCRIPTION} characters including the AI line "
                                  f'"{AI_LINE}" ({len(description)} with it)')
    alt_text = _text(alt_text, MAX_ALT, "alt_text", required=False)
    ref = str(image or "").strip()
    if not ref:
        raise h.CommerceError('image: "folder/name.png", an image name or a Printify mockup address')
    canon, listing_title = _listing(link)
    b = _board(board)
    data = _image(ref)
    from tools.image import providers as prov
    if (mime := prov.sniff_mime(data)) not in IMAGE_MIMES:
        raise h.CommerceError(f"{ref} isn't a JPEG or PNG")
    sha = hashlib.sha256(data).hexdigest()
    again = [r for r in _made() if r.get("sha256") == sha and r.get("link") == canon
             and time.time() - float(r.get("at") or 0) < REPEAT_S]
    if again:
        raise h.CommerceError("this image with this link was pinned in the last 30 days: use another image "
                              "(the same image and link again looks like spam to Pinterest)")
    _listing_gap(canon)
    import printify_pending
    if any(i.get("action") == "pinterest_pin" and (i.get("payload") or {}).get("link") == canon
           for i in printify_pending.list_pending()):
        raise h.CommerceError("a pin of this listing is already waiting for the owner: one pin per listing a week")
    from tools.etsy.write import _thumb
    thumb = _thumb(data)
    short = ref.rsplit("/", 1)[-1] if not ref.startswith("https://") else "a Printify mockup"
    summary = (f'Pin "{h.trim(title, 70)}" to the board "{h.trim(b.get("name"), 40)}", linking to "{listing_title}"'
               + (" (AI-assisted)" if ai_generated else "") + (" in the sandbox" if pc.sandbox() else ""))
    detail = "\n\n".join(s for s in (f"TITLE\n{title}", f"DESCRIPTION\n{description}", f"LINK\n{canon}",
                                     f"BOARD\n{b.get('name')}", f"ALT TEXT\n{alt_text}" if alt_text else "",
                                     f"IMAGE\n{short}") if s)
    import printify_pending
    checks = {"board": b.get("name"), "link": canon, "ai_generated": ai_generated, "sandbox": pc.sandbox(),
              "pinned_last_24h": len(printify_pending.done_today("pinterest_pin")), "daily_max": daily_max()}
    payload = {"title": title, "description": description, "link": canon, "board_id": str(b.get("id")),
               "board": b.get("name"), "alt_text": alt_text, "ai_generated": ai_generated, "sandbox": pc.sandbox(),
               "send": {"images": [{"image": ref, "sha256": sha, "mime": mime}]}, "detail": detail,
               **({"thumbs": [thumb]} if thumb else {})}
    return _queue(f"pinterest-pin-{sha[:12]}", summary, checks, "pinterest_pin", payload)


def _listing_gap(link: str) -> None:
    last = max((float(r.get("at") or 0) for r in _made() if r.get("link") == link), default=0.0)
    if time.time() - last < LISTING_GAP_S:
        days = max(1, round((LISTING_GAP_S - (time.time() - last)) / 86400))
        raise h.CommerceError(f"this listing was pinned in the last 7 days: one pin per listing a week (next in about "
                              f"{days} day{'s' if days != 1 else ''})")


def _same_env(payload: dict) -> None:
    if bool(payload.get("sandbox")) != pc.sandbox():
        raise h.CommerceError("it was asked for the " + ("sandbox" if payload.get("sandbox") else "public account")
                              + ", and Pinterest is now switched to the other: decline it and ask again")


def execute_pin(payload: dict) -> dict:
    """The pin itself, run only by printify_pending.decide on the owner's approval: the image is fetched again and must
    be the one approved, and the listing must still be on sale."""
    _same_env(payload)
    img = ((payload.get("send") or {}).get("images") or [{}])[0]
    data = _image(img.get("image") or "")
    if hashlib.sha256(data).hexdigest() != img.get("sha256"):
        raise h.CommerceError("the image has changed since it was asked: decline this and ask again")
    _listing(payload.get("link") or "")
    _listing_gap(payload.get("link") or "")  # again: another pin of it may have been approved meanwhile
    body = {"board_id": payload["board_id"], "title": payload["title"], "description": payload["description"],
            "link": payload["link"],
            "media_source": {"source_type": "image_base64", "content_type": img.get("mime") or "image/jpeg",
                             "data": base64.b64encode(data).decode()},
            **({"alt_text": payload["alt_text"]} if payload.get("alt_text") else {}),
            **({"ai_disclosures": {"values": ["AI_MODIFIED"]}} if payload.get("ai_generated") else {})}
    out = pc.call("POST", "/v5/pins", json=body)
    pid = str(out.get("id") or "")
    if not pid:
        raise h.CommerceError("Pinterest didn't say it made the pin")
    # Only what we made (Pinterest's guidelines: nothing read from the API is stored): the new pin's id, our image's
    # fingerprint and our listing link, for the repeat and weekly checks.
    _record({"at": time.time(), "pin_id": pid, "sha256": img.get("sha256"), "link": payload["link"],
             "sandbox": bool(payload.get("sandbox")), "ai_generated": bool(payload.get("ai_generated"))})
    return {"pin_id": pid, "url": f"https://www.pinterest.com/pin/{pid}/", "sandbox": bool(payload.get("sandbox")),
            "note": "Pinned." + (" Trial access: only you see it, on your own profile." if payload.get("sandbox") else "")}


def execute_board(payload: dict) -> dict:
    _same_env(payload)
    name = str(payload.get("name") or "")
    have = next((b for b in _boards() if str(b.get("name") or "").strip().lower() == name.lower()), None)
    if have:  # made by an earlier try of this approval, or by hand: never twice
        return {"board_id": str(have.get("id")), "name": name, "made": "earlier"}
    out = pc.call("POST", "/v5/boards", json={"name": name, "privacy": payload.get("privacy") or "PUBLIC",
                                               **({"description": payload["description"]} if payload.get("description") else {})})
    return {"board_id": str(out.get("id") or ""), "name": name, "made": "now",
            "note": "Board made." + (" Trial access: only you see it." if payload.get("sandbox") else "")}
