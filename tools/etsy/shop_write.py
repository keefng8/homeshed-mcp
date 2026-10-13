"""etsy.shop.update (2026-10-10, the owner's decision): ask to set the shop's own texts
(title, announcement, the messages buyers get after a purchase, extra policies) and add shop sections. See
../../capabilities/etsy/write.md.

It changes the public shop, so a call only checks it and queues it (printify_pending.py, action "etsy_shop"); the owner
reads the full new texts on the approval card and approves. Etsy's API has no "About" section (updateShop, OpenAPI spec
3.0.0, checked 2026-10-10): that one is pasted in Shop Manager. Needs a connection made with shops_w.
"""
from __future__ import annotations

import os

from registry import tool
from tools.commerce import _http as h
from tools.etsy import _client as et

LABEL = "Editing your Etsy shop"
# Etsy's updateShop fields -> (this tool's cap in characters, what the owner's card calls it). The shop title's 55 is
# Etsy's own limit; the others are this tool's.
FIELDS = {"title": (55, "shop title"), "announcement": (5000, "announcement"),
          "sale_message": (5000, "message to buyers"), "digital_sale_message": (5000, "message to digital buyers"),
          "policy_additional": (5000, "additional policies")}
MAX_SECTION_CHARS, MAX_NEW_SECTIONS = 24, 10  # Etsy's section title limit; this tool's per request
PENDING_KEY = "etsy-shop"  # one shop request waits at a time


def daily_max() -> int:
    try:
        return max(0, min(50, int(os.environ.get("ETSY_SHOP_EDIT_DAILY_MAX") or 10)))
    except ValueError:
        return 10


def _sections() -> list[dict]:
    d = et.get(f"/v3/application/shops/{et.shop_id()}/sections") or {}
    return [s for s in d.get("results") or [] if isinstance(s, dict)]


def _check_sections(sections) -> list[str]:
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_NEW_SECTIONS:
        raise h.CommerceError(f"sections: 1-{MAX_NEW_SECTIONS} section names at a time")
    out: list[str] = []
    for s in sections:
        name = " ".join(str(s or "").split())
        if not 1 <= len(name) <= MAX_SECTION_CHARS:
            raise h.CommerceError(f'section "{name}": 1-{MAX_SECTION_CHARS} characters (Etsy\'s limit)')
        if name.lower() not in (o.lower() for o in out):
            out.append(name)
    return out


@tool(name="shop.update", category="etsy", doc="etsy/write.md")
def shop_update(title: str | None = None, announcement: str | None = None, sale_message: str | None = None,
                digital_sale_message: str | None = None, policy_additional: str | None = None,
                sections: list[str] | None = None, owner_approval: dict | None = None) -> dict:
    """Ask to set the Etsy shop's own texts and add shop sections. It is NOT changed by this call: it's checked against
    the shop, then waits for the owner's approval in the Control Panel, where the full new texts show. Only what
    differs is sent. Etsy's API has no "About" section: paste that in Shop Manager. Off until the owner switches "Let
    apps ask to edit your Etsy shop" on; needs Etsy connected with shop edits.

    Args:
        title: the shop title, the heading on the shop's main page (up to 55 characters); omit to keep.
        announcement: the announcement on the shop's homepage (up to 5,000 characters); "" clears it; omit to keep.
        sale_message: the message buyers get after a purchase (up to 5,000); "" clears it; omit to keep.
        digital_sale_message: the message buyers of a digital item get (up to 5,000); omit to keep.
        policy_additional: the shop's additional policies (up to 5,000); omit to keep.
        sections: 1-10 shop section names to add (up to 24 characters each); ones the shop has already are skipped.
            Put a listing in a section with etsy.listing.update shop_section_id.
        owner_approval: {"id", "by", "at"}: the owner's own approval inside a trusted app (as printify.product.publish);
            then it's set at once.

    Returns:
        {"pending": true, "id", "etsy_shop": summary, "expires_in_h", "how"}: the approval request, not a change on
        the shop; {"pending": false} when the shop is already as asked.
    """
    h.require(et.SETTING, "Etsy")
    h.require(et.SHOP_WRITE_SETTING, LABEL)
    asked = {k: v for k, v in (("title", title), ("announcement", announcement), ("sale_message", sale_message),
                               ("digital_sale_message", digital_sale_message), ("policy_additional", policy_additional))
             if v is not None}
    fields: dict[str, str] = {}
    for k, v in asked.items():
        text = str(v).strip()
        cap, what = FIELDS[k]
        if len(text) > cap:
            raise h.CommerceError(f"{k}: at most {cap} characters ({len(text)} given)")
        if k == "title" and not text:
            raise h.CommerceError("title can't be empty")
        fields[k] = text
    new_sections = _check_sections(sections) if sections is not None else []
    if not fields and not new_sections:
        raise h.CommerceError("nothing to change: give a title, announcement, sale_message, digital_sale_message, "
                              "policy_additional or sections")
    shop = et.get(f"/v3/application/shops/{et.shop_id()}") or {}
    send = {k: v for k, v in fields.items() if str(shop.get(k) or "").strip() != v}
    if new_sections:
        have = {str(s.get("title") or "").strip().lower() for s in _sections()}
        new_sections = [s for s in new_sections if s.lower() not in have]
    if not send and not new_sections:
        return et.note({"pending": False, "note": "already as asked: nothing queued"})
    said = [f"{FIELDS[k][1]}: " + (f'"{h.trim(v, 60)}"' if v else "cleared") for k, v in send.items()]
    if new_sections:
        said.append("new sections: " + ", ".join(new_sections))
    summary = "Set on your Etsy shop: " + "; ".join(said)
    detail = "\n\n".join([f"{FIELDS[k][1].upper()}\n{v or '(cleared)'}" for k, v in send.items()]
                         + ([("NEW SECTIONS\n" + "\n".join(new_sections))] if new_sections else []))
    import printify_pending
    checks = {"fields": sorted(send), "sections": new_sections,
              "edited_last_24h": len(printify_pending.done_today("etsy_shop")), "daily_max": daily_max()}
    payload = {"send": send, "sections": [{"title": s} for s in new_sections], "detail": detail}
    try:
        return printify_pending.add(PENDING_KEY, h.trim(summary, 300), checks, action="etsy_shop", payload=payload,
                                    owner_approval=owner_approval)
    except printify_pending.PendingError as exc:
        raise h.CommerceError(str(exc)) from None


def execute(payload: dict) -> dict:
    """The change itself, run only by printify_pending.decide on the owner's approval. A section the shop already has
    (made by an earlier try of this approval, or by hand) isn't made twice."""
    shop = et.shop_id()
    send = payload.get("send") or {}
    if send:
        et.write("PUT", f"/v3/application/shops/{shop}", send, et.SHOP_WRITE_SETTING, LABEL)
    have = {str(s.get("title") or "").strip().lower(): s.get("shop_section_id") for s in _sections()}
    made = []
    for sec in payload.get("sections") or []:
        name = str(sec.get("title") or "")
        if sec.get("done_id") or name.lower() in have:
            made.append({"title": name, "shop_section_id": sec.get("done_id") or have.get(name.lower()),
                         "made": "earlier"})
            continue
        out = et.write("POST", f"/v3/application/shops/{shop}/sections", {"title": name}, et.SHOP_WRITE_SETTING, LABEL)
        sec["done_id"] = (out or {}).get("shop_section_id") or True  # kept on the request (printify_pending saves it)
        made.append({"title": name, "shop_section_id": sec["done_id"], "made": "now"})
    return {"fields": sorted(send), "sections": made,
            "note": "Set on your Etsy shop." + (" Put listings in the new sections with etsy.listing.update "
                                                "shop_section_id." if made else "")}
