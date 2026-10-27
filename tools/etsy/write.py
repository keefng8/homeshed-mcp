"""etsy.listing.update and etsy.listing.deactivate (2026-10-08: both behind one listings_w
reconnect). See ../../capabilities/etsy/write.md.

etsy.listing.update sets the details of one of the shop's listings that Printify doesn't manage: its category
(taxonomy), materials, production partners, delivery profile, and attributes (Etsy "properties": colour, occasion,
recipient, holiday...). It changes a public listing, so a call only checks it and queues it (printify_pending.py,
action "etsy"); the owner approves it in the Control Panel. Title, description, tags and prices go through Printify
(printify.product.update_live): set here, Printify's next publish would undo them.

The re-publish rule: details are set after a listing's last publish, and a re-publish never leaves them undone. What
was last set on each listing is kept (ATTRS_FILE). An approved printify.product.update_live of that listing that
re-sends its variants, images or delivery template queues them again straight after (queue_reapply), for the owner to
approve once Printify has finished publishing; a words-only one doesn't (update_live.RESETS_ETSY_DETAILS).

etsy.listing.deactivate takes a listing off sale at once (state inactive). It's a brake, not a publish, so it runs
without an approval, behind its own switch, capped, audited, and the owner gets a push each time. Etsy's Shop Manager
re-activates it.

Processing time isn't here. Etsy moved it into processing profiles set on each inventory offering
(readiness_state_id), and writing the inventory resends every SKU and price, which can unlink Printify's orders.
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

import httpx

from paths import data_path
from registry import tool
from tools.commerce import _http as h
from tools.etsy import _client as et

ATTRS_FILE = Path(os.environ.get("ETSY_ATTRIBUTES_FILE") or data_path("usage/etsy_attributes.json"))
DEACTIVATED_FILE = Path(os.environ.get("ETSY_DEACTIVATED_FILE") or data_path("usage/etsy_deactivated.json"))
LABEL = "Editing Etsy listings"
DEACTIVATE_LABEL = "Deactivating Etsy listings"
MAX_MATERIALS = 13
MAX_NEW_IMAGES = 5                  # per request
MAX_LISTING_IMAGES = 10             # Etsy's limit per listing
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # this tool's cap
IMAGE_MIMES = ("image/jpeg", "image/png", "image/gif")
_lock = threading.Lock()


def _env_int(name: str, default: int, ceiling: int = 100) -> int:
    try:
        return max(0, min(ceiling, int(os.environ.get(name) or default)))
    except ValueError:
        return default


def edit_daily_max() -> int:
    # 20 until 2026-10-09: a new listing needs 2 edits (the owner's OK). The ceiling went from 100 to 200 on 2026-10-14
    # (the owner: "Raise the limit to reach target", 150): 30 publishes a night need 60 edits, plus repairs. 500 on
    # 2026-10-18 (the owner: "raise the edit limit on etsy to 500 now. i need it").
    return _env_int("ETSY_EDIT_DAILY_MAX", 50, ceiling=500)


def deactivate_daily_max() -> int:
    return _env_int("ETSY_DEACTIVATE_DAILY_MAX", 5)


def _read(path: Path, key: str, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(key) or default
    except (OSError, ValueError, AttributeError):
        return default


def _write(path: Path, key: str, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({key: value}, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def applied() -> dict:
    """{listing id: {"fields", "properties", "title", "at"}}: what this tool last set on each listing."""
    rows = _read(ATTRS_FILE, "listings", {})
    return rows if isinstance(rows, dict) else {}


def _own_listing(lid: str) -> dict:
    x = et.get(f"/v3/application/listings/{lid}") or {}  # signed in: an inactive or draft listing isn't public
    if not isinstance(x, dict) or str(x.get("shop_id")) != et.shop_id():
        raise h.CommerceError("that listing isn't in this shop")
    return x


def _positive(value, what: str) -> int | None:
    if value in (None, 0, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise h.CommerceError(f"{what} must be a positive whole number")
    return value


def _check_fields(taxonomy_id, materials, production_partner_ids, shipping_profile_id, shop_section_id=0) -> dict:
    fields: dict = {}
    for name, value in (("taxonomy_id", taxonomy_id), ("shipping_profile_id", shipping_profile_id),
                        ("shop_section_id", shop_section_id)):
        if (v := _positive(value, name)) is not None:
            fields[name] = v
    if materials is not None:
        mats = [str(m).strip() for m in materials if str(m).strip()]
        if len(mats) > MAX_MATERIALS or any(len(m) > 45 or not re.fullmatch(r"(?:[^\W_]| )+", m) for m in mats):
            raise h.CommerceError(f"materials: at most {MAX_MATERIALS}, each up to 45 letters, digits and spaces "
                                  "(Etsy's rule)")
        fields["materials"] = mats
    if production_partner_ids is not None:
        ids = [_positive(p, "each production partner id") for p in production_partner_ids]
        fields["production_partner_ids"] = sorted(set(ids))
    return fields


def _resolve(taxonomy_id: int, wanted: dict) -> list[dict]:
    """{"Primary color": "Black", "Occasion": ["Christmas"]} -> [{"property_id", "name", "value_ids", "values",
    "scale_id"?}], matched without case against the category's own attributes and values (Etsy refuses others)."""
    d = et.get(f"/v3/application/seller-taxonomy/nodes/{taxonomy_id}/properties", oauth=False) or {}
    props = [p for p in d.get("results") or [] if isinstance(p, dict) and p.get("supports_attributes")]
    names = sorted(str(p.get("display_name") or p.get("name")) for p in props)
    out = []
    for name, value in wanted.items():
        vals = [value] if isinstance(value, str) else list(value) if isinstance(value, list) else []
        if value is not None and (not vals or not all(isinstance(v, str) and v.strip() for v in vals)):
            raise h.CommerceError(f'"{name}": give a value, a list of values, or null to remove it')
        key = str(name).strip().lower()
        prop = next((p for p in props if key in (str(p.get("name")).lower(), str(p.get("display_name")).lower())), None)
        if prop is None:
            raise h.CommerceError(h.trim(f'"{name}" isn\'t an attribute of category {taxonomy_id}; it has: '
                                         + ", ".join(names), 500))
        if value is None:  # remove it (2026-10-14: "Holiday": "Christmas" had gone on year-round tees)
            out.append({"property_id": prop["property_id"], "name": str(prop.get("display_name") or prop.get("name")),
                        "value_ids": [], "values": [], "remove": True})
            continue
        cap = prop.get("max_values_allowed") if prop.get("is_multivalued") else 1
        if isinstance(cap, int) and cap and len(vals) > cap:
            raise h.CommerceError(f'"{name}" takes at most {cap} value(s)')
        possible = {str(v.get("name")).lower(): v for v in prop.get("possible_values") or [] if isinstance(v, dict)}
        if not possible:
            raise h.CommerceError(f'"{name}" is a free-text attribute; only attributes with a list of values are set here')
        picked = []
        for v in vals:
            pv = possible.get(v.strip().lower())
            if pv is None:
                raise h.CommerceError(h.trim(f'"{name}": "{v}" isn\'t one of Etsy\'s values ('
                                             + ", ".join(sorted(str(x.get("name")) for x in possible.values())) + ")", 500))
            picked.append(pv)
        scales = {pv.get("scale_id") for pv in picked if pv.get("scale_id")}
        if len(scales) > 1:
            raise h.CommerceError(f'"{name}": those values are on different measurement scales')
        out.append({"property_id": prop["property_id"], "name": str(prop.get("display_name") or prop.get("name")),
                    "value_ids": [pv["value_id"] for pv in picked], "values": [str(pv["name"]) for pv in picked],
                    **({"scale_id": scales.pop()} if scales else {})})
    return out


def _image(ref: str) -> bytes:
    """One photo's bytes (tools/image/refs.py: a Printify mockup, "folder/name.png", a kept or a generated image)."""
    from tools.image.refs import image_bytes
    return image_bytes(ref, MAX_IMAGE_BYTES)


def _thumb(data: bytes) -> str | None:
    """A small JPEG (base64) for the owner's approval card, made by the image service; None when it can't (the card
    then lists the images by name)."""
    from tools.image.generate import base_url
    try:
        r = httpx.post(f"{base_url()}/image/thumb", json={"image_b64": base64.b64encode(data).decode(), "px": 240},
                       timeout=30)
        return r.json().get("image_b64") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def _check_images(images) -> list[dict]:
    if not isinstance(images, list) or not 1 <= len(images) <= MAX_NEW_IMAGES:
        raise h.CommerceError(f"images: 1-{MAX_NEW_IMAGES} of {{\"image\", \"alt_text\"?, \"rank\"?}} at a time")
    out, ranks = [], set()
    for img in images:
        img = img if isinstance(img, dict) else {}
        ref, alt, rank = str(img.get("image") or "").strip(), str(img.get("alt_text") or "").strip(), img.get("rank")
        if not ref:
            raise h.CommerceError('each image needs "image": its name, "folder/name.png" or a Printify mockup address')
        if len(alt) > 500:
            raise h.CommerceError("alt_text: at most 500 characters (Etsy's limit)")
        if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int)
                                 or not 1 <= rank <= MAX_LISTING_IMAGES or rank in ranks):
            raise h.CommerceError(f"rank: a different whole number 1-{MAX_LISTING_IMAGES} for each image")
        ranks.add(rank)
        out.append({"image": ref, **({"alt_text": alt} if alt else {}), **({"rank": rank} if rank else {})})
    return out


def _short_ref(ref: str) -> str:
    return "a Printify mockup" if ref.startswith("https://") else ref.rsplit("/", 1)[-1]


def _said(x: dict, fields: dict, props: list[dict]) -> str:
    """The change in a few words, for the owner's approval card."""
    parts = []
    for k, label in (("taxonomy_id", "category"), ("shipping_profile_id", "delivery profile"),
                     ("shop_section_id", "shop section")):
        if k in fields:
            parts.append(f"{label} {x[k]} -> {fields[k]}" if x.get(k) else f"{label} {fields[k]}")
    if "materials" in fields:
        parts.append("materials: " + (", ".join(fields["materials"]) or "none"))
    if "production_partner_ids" in fields:
        parts.append("production partners: " + (", ".join(map(str, fields["production_partner_ids"])) or "none"))
    parts += [f"{p['name']}: removed" if p.get("remove") else f"{p['name']}: {', '.join(p['values'])}" for p in props]
    return "; ".join(parts)


def _queue(lid: str, title: str, summary: str, send: dict, wanted: dict, reapply: bool,
           thumbs: list | None = None, owner_approval: dict | None = None) -> dict:
    import printify_pending
    checks = {"listing_id": lid, "fields": sorted(send["fields"]), "attributes": [p["name"] for p in send["properties"]],
              "images": len(send.get("images") or []), "reapply": reapply,
              "edited_last_24h": len(printify_pending.done_today("etsy")), "daily_max": edit_daily_max()}
    payload = {"listing_id": lid, "title": title, "send": send, "wanted": wanted, **({"thumbs": thumbs} if thumbs else {})}
    try:
        return printify_pending.add(f"etsy-{lid}", h.trim(summary, 300), checks, action="etsy", payload=payload,
                                    owner_approval=owner_approval)
    except printify_pending.PendingError as exc:
        raise h.CommerceError(str(exc)) from None


@tool(name="listing.update", category="etsy", doc="etsy/write.md")
def listing_update(listing_id: str, taxonomy_id: int = 0, materials: list[str] | None = None,
                   production_partner_ids: list[int] | None = None, shipping_profile_id: int = 0,
                   properties: dict | None = None, reapply: bool = False, images: list[dict] | None = None,
                   owner_approval: dict | None = None, shop_section_id: int = 0,
                   remove_images: list[int] | None = None, images_alt: list[dict] | None = None,
                   swap: bool = False, hero_swap: bool = False) -> dict:
    """Ask to set an Etsy listing's details that Printify doesn't manage: category, materials, production partners,
    delivery profile, attributes (colour, occasion, recipient, holiday...) and extra photos. It is NOT changed by this
    call: it's checked against the listing and Etsy's own lists, then waits for the owner's approval in the Control
    Panel (photos show there as thumbnails). Only what differs is sent. Title, tags and prices:
    printify.product.update_live. Off until the owner switches "Let apps ask to edit your Etsy listings' details" on;
    needs Etsy connected with listing edits.

    Args:
        listing_id: the listing's number (etsy.listings.list).
        taxonomy_id: the category (Etsy's seller taxonomy id); 0 keeps it.
        materials: the full materials list (up to 13, letters, digits and spaces, 45 characters each); omit to keep.
        production_partner_ids: the full list of the shop's production partner ids; omit to keep.
        shipping_profile_id: the delivery profile; 0 keeps it.
        properties: {"attribute name": "value" or ["values"]}, e.g. {"Primary color": "Black", "Holiday":
            "Christmas"}, matched to the category's attributes and Etsy's values (a wrong one is refused with the list).
            null removes the attribute ({"Holiday": null}), and it is never re-applied.
        reapply: set again everything this tool last set on the listing (after a re-publish undid it); the other
            arguments are then ignored. Photos aren't re-applied (a Printify update never re-sends photos).
        images: 1-5 photos to add, [{"image": ref, "alt_text"?: up to 500 characters, "rank"?: 1-10}], where ref is
            "folder/name.png" (saved in the image store under its own name, e.g. "listing/krc-staffy-artwork-2400.png"),
            an image name, or a Printify mockup address. JPEG, PNG or GIF up to 10 MB; the listing holds at most 10.
            Each photo is fingerprinted now, and checked again before it's uploaded on approval.
        owner_approval: {"id", "by", "at"}: the owner's own approval inside a trusted app (as printify.product.publish);
            then it's set at once.
        shop_section_id: the shop section to show the listing in (etsy.shop.update makes sections); 0 keeps it.
        remove_images: listing_image_ids to delete from the listing (etsy.listing.get lists them), e.g. raw artwork
            being swapped for a watermarked copy. Can't be undone; never the listing's last photo (add the new ones in
            the same call: they go up first).
        images_alt: new alt text for photos already on the listing, [{"listing_image_id", "alt_text": up to 500}].
        swap: true for a raw-artwork photo swap (the owner, 2026-10-20, design theft): an AUTO or bot approval may
            delete a photo ONLY as a swap that passes every guard: one photo removed and its watermarked replacement
            added in the same call; the removed one's alt text says "artwork", it is a 2048 or 2400 px square, not at
            rank 1 or 2, and not a mockup, a "worn in" hero or an info card; at most ETSY_SWAP_DAILY_MAX (60) a day.
            On approval the replacement is read back on Etsy and the old photo backed up (file and link) before the
            delete. Fail any guard and an automatic approval isn't used: it waits for the owner's tap.
        hero_swap: true to replace the AI lifestyle hero with its copy carrying the AI disclosure bar (the owner's
            go, 2026-10-22): an automatic approval may delete ONLY when one photo is removed and one added in the same
            call, the removed one is at rank 1, both alt texts read as a hero (ETSY_HERO_ALT, default "worn in"; never
            "artwork"), and a photo that isn't an AI scene (the mockup) stays on the listing. Same read-back, backup
            and daily maximum as swap.

    Returns:
        {"pending": true, "id", "etsy": summary, "expires_in_h", "how"}: the approval request, not a change on the
        shop; {"pending": false} when the listing is already as asked.
    """
    lid = h.check_id(listing_id, "listing_id")
    h.require(et.SETTING, "Etsy")
    h.require(et.WRITE_SETTING, LABEL)
    if reapply:
        saved = applied().get(lid)
        if not saved:
            raise h.CommerceError("nothing was set on this listing by this tool, so there's nothing to re-apply")
        fields, props = dict(saved.get("fields") or {}), list(saved.get("properties") or [])
    else:
        fields = _check_fields(taxonomy_id, materials, production_partner_ids, shipping_profile_id, shop_section_id)
        if properties is not None and not isinstance(properties, dict):
            raise h.CommerceError('properties must be {"attribute name": "value" or ["values"]}')
        if not fields and not properties and not images and not remove_images and not images_alt:
            raise h.CommerceError("nothing to change: give a category, materials, production partners, a delivery "
                                  "profile, a shop section, attributes, images, remove_images or images_alt")
        props = []
    new_images = _check_images(images) if images and not reapply else []
    x = _own_listing(lid)
    thumbs = []
    removes, alts = ([], []) if reapply else (_check_photo_edits(remove_images, images_alt))
    have_ids = None
    if removes or alts:
        listed = (et.get(f"/v3/application/listings/{lid}/images") or {}).get("results") or []
        have_ids = {int(i["listing_image_id"]) for i in listed if isinstance(i, dict) and i.get("listing_image_id")}
        if missing := sorted({*removes, *(a["listing_image_id"] for a in alts)} - have_ids):
            raise h.CommerceError(f"not photos of this listing: {missing} (etsy.listing.get lists its photos)")
        if len(have_ids) - len(set(removes)) + len(new_images) < 1:
            raise h.CommerceError("that would leave the listing with no photo: add the new one in the same call")
        if set(removes) & {a["listing_image_id"] for a in alts}:
            raise h.CommerceError("a photo can't be both removed and given new alt text")
    if new_images:
        have_n = len(have_ids) if have_ids is not None else \
            (et.get(f"/v3/application/listings/{lid}/images") or {}).get("count") or 0
        if have_n - len(set(removes)) + len(new_images) > MAX_LISTING_IMAGES:
            raise h.CommerceError(f"the listing has {have_n} photos; {len(new_images)} more would pass Etsy's "
                                  f"{MAX_LISTING_IMAGES}")
        for img in new_images:
            data = _image(img["image"])
            from tools.image import providers as prov
            if (mime := prov.sniff_mime(data)) not in IMAGE_MIMES:
                raise h.CommerceError(f"{img['image']} isn't a JPEG, PNG or GIF")
            img.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), mime=mime)
            thumbs.append(_thumb(data))
        if not any(thumbs):
            thumbs = []
    if not reapply and properties:
        tax = fields.get("taxonomy_id") or x.get("taxonomy_id")
        if not isinstance(tax, int):
            raise h.CommerceError("the listing has no category yet: give taxonomy_id too")
        props = _resolve(tax, properties)
    now = {k: et.partner_ids(x) if k == "production_partner_ids" else x.get(k) for k in fields}
    send_fields = {k: v for k, v in fields.items() if now.get(k) != v}
    cur = et.get(f"/v3/application/shops/{et.shop_id()}/listings/{lid}/properties", oauth=False) or {}
    have = {p.get("property_id"): sorted(p.get("value_ids") or []) for p in cur.get("results") or [] if isinstance(p, dict)}
    send_props = [p for p in props if (bool(have.get(p["property_id"])) if p.get("remove")
                                       else have.get(p["property_id"]) != sorted(p["value_ids"]))]
    if gone := {p["property_id"] for p in props if p.get("remove")}:
        _forget_props(lid, gone)  # at once, even if Etsy already lacks it: a re-apply must never set it back
    if not send_fields and not send_props and not new_images and not removes and not alts:
        return {"listing_id": lid, "pending": False, "note": "already as asked: nothing queued"}
    title = h.trim(x.get("title"), 60) or "untitled"
    said = "; ".join(s for s in (_said(x, send_fields, send_props),
                                 ("photos: " + ", ".join(_short_ref(i["image"]) for i in new_images)) if new_images else "",
                                 f"delete photos {removes} (can't be undone)" if removes else "",
                                 f"new alt text on photos {[a['listing_image_id'] for a in alts]}" if alts else "")
                     if s)
    summary = f'{"Re-apply" if reapply else "Set"} Etsy details on "{title}": {said}'
    send = {"fields": send_fields, "properties": send_props, "images": new_images, "remove_images": removes,
            "images_alt": alts}
    held = None
    if removes and _automatic(owner_approval):  # a machine never approves a delete outside a guarded swap
        held = (_hero_swap_problem(removes, new_images, listed, alts) if hero_swap and not swap
                else _swap_problem(swap, removes, new_images, listed)) or None
        if held and _by(owner_approval) in _delete_bots():
            # a named bot's own delete (approvals_photo_delete_bots): its own guards, cap and log, instead of a swap's
            if not (held := _bot_delete_problem(removes, new_images, listed) or None):
                send["swap_kind"], send["delete_reason"] = "bot", summary
        if held:
            owner_approval = None
        else:
            send["swap"] = True  # execute() reads the replacement back and backs the old photo up first
    out = _queue(lid, title, summary, send, {"fields": fields, "properties": props}, reapply, thumbs, owner_approval)
    return {**out, "swap_held": held} if held else out


SWAP_SIDES = (2048, 2400)  # the raw artwork's square sizes (guard 1)
SWAP_NEVER = ("mockup", "worn in", "info", "size guide", "size chart")  # guard 5: photos a swap never deletes
SWAP_FILE = Path(os.environ.get("ETSY_SWAPS_FILE") or data_path("usage/etsy_photo_swaps.json"))
SWAP_BACKUPS = Path(os.environ.get("ETSY_SWAP_BACKUP_DIR") or data_path("usage/etsy_photo_backups"))


def swap_daily_max() -> int:
    return _env_int("ETSY_SWAP_DAILY_MAX", 60, ceiling=60)


def _by(ok) -> str:
    return str((ok or {}).get("by") or "").strip().lower() if isinstance(ok, dict) else ""


def _automatic(ok) -> bool:
    return _by(ok).startswith(("auto", "bot:"))


def _delete_bots() -> set[str]:
    """The "bot:<name>" approvals that may delete photos outside a swap (approvals_photo_delete_bots); none unless the
    owner names one."""
    from printify_pending import trusted_clients
    return {f"bot:{b.lower()}" for b in trusted_clients(DELETE_BOTS_SETTING)}


DELETE_BOTS_SETTING = "approvals_photo_delete_bots"


def _bot_delete_problem(removes: list[int], new_images: list[dict], listed: list) -> str:
    """Why a named bot's approval can't delete these photos, or "" when every limit passes: each one has a
    replacement in the same request, the listing keeps a photo it has now, rank 1 is never deleted, and at most
    swap_daily_max() a day."""
    photos = [p for p in listed if isinstance(p, dict)]
    if not new_images or len(new_images) < len(removes):
        return "a bot's delete adds a replacement for each photo in the same request: it waits for the owner"
    if not any(int(p.get("listing_image_id") or 0) not in removes for p in photos):
        return "that would delete every photo the listing has now: it waits for the owner"
    first = min((p for p in photos if isinstance(p.get("rank"), int)), key=lambda p: p["rank"], default={})
    if int(first.get("listing_image_id") or 0) in removes:
        return "a bot's delete never removes the rank-1 photo: it waits for the owner"
    done = sum(len(r.get("removed") or []) for r in _swaps_today() if r.get("kind") == "bot")
    if done + len(removes) > swap_daily_max():
        return f"{done} of {swap_daily_max()} bot photo deletes used in the last 24 hours: this waits for the owner"
    return ""


def _swaps_today() -> list[dict]:
    try:
        rows = json.loads(SWAP_FILE.read_text(encoding="utf-8")).get("swaps") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    return [r for r in rows if isinstance(r, dict) and time.time() - float(r.get("at") or 0) < 86400]


def _swap_problem(swap: bool, removes: list[int], new_images: list[dict], listed: list) -> str:
    """Why an automatic approval can't delete this photo, or "" when every guard passes (the owner's yes, 2026-10-20,
    "only if mistakes are mitigated first")."""
    if not swap:
        return "a photo delete with an automatic approval must be a swap (swap=true): it waits for the owner"
    if len(removes) != 1:
        return "a swap deletes exactly one photo: it waits for the owner"
    if not new_images:
        return "a swap adds its watermarked replacement in the same request: it waits for the owner"
    photo = next((p for p in listed if isinstance(p, dict) and int(p.get("listing_image_id") or 0) == removes[0]), {})
    alt = str(photo.get("alt_text") or "").lower()
    w, hgt, rank = photo.get("full_width"), photo.get("full_height"), photo.get("rank")
    if "artwork" not in alt:
        return "the photo's alt text doesn't say \"artwork\": it waits for the owner"
    if any(word in alt for word in SWAP_NEVER):
        return "the photo looks like a mockup, a worn-in hero or an info card: it waits for the owner"
    if not (isinstance(w, int) and w == hgt and w in SWAP_SIDES):
        return f"the photo is {w}x{hgt}, not a 2048 or 2400 px square: it waits for the owner"
    if not isinstance(rank, int) or rank <= 2:
        return f"the photo is at rank {rank}: a swap never deletes rank 1 or 2, it waits for the owner"
    if sum(r.get("kind") != "bot" for r in _swaps_today()) >= swap_daily_max():
        return f"{swap_daily_max()} automatic swaps in the last 24 hours: this one waits for the owner"
    return ""


HERO_ALT_DEFAULT = "worn in"  # what a shop's AI lifestyle hero alt text says; ETSY_HERO_ALT (a regex) sets its own
NOT_A_MOCKUP = ("artwork", "size guide", "size chart", "colours", "colors", "printed to order", "info")


def hero_alt() -> re.Pattern:
    """The hero alt-text pattern: ETSY_HERO_ALT, or "worn in". A broken pattern falls back to the default."""
    try:
        return re.compile(os.environ.get("ETSY_HERO_ALT") or HERO_ALT_DEFAULT, re.IGNORECASE)
    except re.error:
        return re.compile(HERO_ALT_DEFAULT, re.IGNORECASE)


def _hero_swap_problem(removes: list[int], new_images: list[dict], listed: list, alts: list[dict] | None = None) -> str:
    """Why an automatic approval can't swap this hero for its barred copy, or "" when every guard passes (2026-10-22:
    one out, one in, both read as a hero, rank 1, and a non-AI photo stays)."""
    HERO_ALT = hero_alt()
    if len(removes) != 1 or len(new_images) != 1:
        return "a hero swap removes exactly one photo and adds exactly one: it waits for the owner"
    photo = next((p for p in listed if isinstance(p, dict) and int(p.get("listing_image_id") or 0) == removes[0]), {})
    old_alt, new_alt = str(photo.get("alt_text") or ""), str(new_images[0].get("alt_text") or "")
    if not HERO_ALT.search(old_alt) or "artwork" in old_alt.lower():
        return "the removed photo's alt text doesn't read as an AI hero: it waits for the owner"
    if not HERO_ALT.search(new_alt) or "artwork" in new_alt.lower():
        return "the added photo's alt text doesn't read as a hero: it waits for the owner"
    if photo.get("rank") != 1:
        return f"the hero is at rank {photo.get('rank')}: a hero swap deletes only rank 1, it waits for the owner"
    # Each photo that stays, with the alt text it will have after this request (images_alt may set it in the same
    # call: 2026-10-23, the Labrador mockup's alt was blank until then). A blank alt counts as non-AI: a hero always
    # carries its hero wording, and Printify's mockups arrive with none.
    new_alt = {a["listing_image_id"]: a["alt_text"] for a in alts or []}
    others = [str(new_alt.get(int(p.get("listing_image_id") or 0), p.get("alt_text")) or "").lower() for p in listed
              if isinstance(p, dict) and int(p.get("listing_image_id") or 0) != removes[0]]
    if not any(not HERO_ALT.search(a) and not any(w in a for w in NOT_A_MOCKUP) for a in others):
        return "no non-AI photo (the mockup) would stay on the listing: it waits for the owner"
    if sum(r.get("kind") != "bot" for r in _swaps_today()) >= swap_daily_max():
        return f"{swap_daily_max()} automatic swaps in the last 24 hours: this one waits for the owner"
    return ""


def _backup_photo(lid: str, iid: int, listed: list) -> dict:
    """The photo's file and link saved before it is deleted, so it can be put back. Raises if it can't be saved: then
    nothing is deleted."""
    photo = next((p for p in listed if isinstance(p, dict) and int(p.get("listing_image_id") or 0) == iid), {})
    url = str(photo.get("url_fullxfull") or "")
    if not url.startswith("https://i.etsystatic.com/"):
        raise h.CommerceError(f"photo {iid} has no Etsy image link to back up: nothing was deleted")
    try:
        r = httpx.get(url, timeout=60, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise h.CommerceError(f"photo {iid} couldn't be backed up ({type(exc).__name__}): nothing was deleted") from None
    if r.status_code != 200 or len(r.content) < 1000:
        raise h.CommerceError(f"photo {iid} couldn't be backed up (HTTP {r.status_code}): nothing was deleted")
    folder = SWAP_BACKUPS / lid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{iid}.jpg").write_bytes(r.content)
    meta = {"listing_id": lid, "listing_image_id": iid, "url": url, "alt_text": photo.get("alt_text"),
            "rank": photo.get("rank"), "width": photo.get("full_width"), "height": photo.get("full_height"),
            "bytes": len(r.content), "at": time.time()}
    (folder / f"{iid}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def _record_swap(row: dict) -> None:
    rows = _swaps_today() + [row]
    SWAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SWAP_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"swaps": rows[-500:]}, indent=1), encoding="utf-8")
    os.replace(tmp, SWAP_FILE)


def _check_photo_edits(remove_images, images_alt) -> tuple[list[int], list[dict]]:
    removes = []
    for i in remove_images or []:
        if isinstance(i, bool) or not str(i).isdigit():
            raise h.CommerceError("remove_images: listing_image_ids (numbers, from etsy.listing.get)")
        removes.append(int(i))
    alts = []
    for a in images_alt or []:
        iid, text = (a or {}).get("listing_image_id"), (a or {}).get("alt_text")
        if isinstance(iid, bool) or not str(iid or "").isdigit() or not isinstance(text, str) or not text.strip():
            raise h.CommerceError('images_alt: [{"listing_image_id": number, "alt_text": "text"}]')
        if len(text) > 500:
            raise h.CommerceError("images_alt: alt_text is at most 500 characters")
        alts.append({"listing_image_id": int(iid), "alt_text": text.strip()})
    if len(removes) + len(alts) > MAX_LISTING_IMAGES:
        raise h.CommerceError(f"at most {MAX_LISTING_IMAGES} photo edits in one call")
    return sorted(set(removes)), alts


def _forget_props(lid: str, property_ids: set) -> None:
    """Drop attributes from what this tool re-applies to a listing (applied())."""
    with _lock:
        rows = applied()
        saved = rows.get(lid)
        if saved and any(p.get("property_id") in property_ids for p in saved.get("properties") or []):
            saved["properties"] = [p for p in saved["properties"] if p.get("property_id") not in property_ids]
            _write(ATTRS_FILE, "listings", rows)


def queue_reapply(lid: str) -> dict | None:
    """After an approved re-publish of this listing (printify.product.update_live): queue everything this tool last
    set on it, whether it looks changed or not (Printify may not have finished overwriting yet). None when nothing
    was set."""
    saved = applied().get(lid)
    if not saved:
        return None
    wanted = {"fields": saved.get("fields") or {}, "properties": saved.get("properties") or []}
    title = str(saved.get("title") or lid)
    summary = (f'Re-apply Etsy details on "{title}" after Printify\'s re-publish (approve once Printify has '
               f'finished, a few minutes): {_said({}, wanted["fields"], wanted["properties"])}')
    return _queue(lid, title, summary, wanted, wanted, True)


def execute(payload: dict) -> dict:
    """The change itself, run only by printify_pending.decide on the owner's approval: the listing is checked to be
    the shop's again, then its fields are patched and each attribute set (all idempotent, so a retry is safe)."""
    lid = h.check_id(payload.get("listing_id"), "listing_id")
    send = payload.get("send") or {}
    fields, props = send.get("fields") or {}, send.get("properties") or []
    _own_listing(lid)
    shop = et.shop_id()
    if fields:
        et.write("PATCH", f"/v3/application/shops/{shop}/listings/{lid}", fields, et.WRITE_SETTING, LABEL)
    for p in props:
        path = f"/v3/application/shops/{shop}/listings/{lid}/properties/{p['property_id']}"
        if p.get("remove"):
            try:
                et.write("DELETE", path, {}, et.WRITE_SETTING, LABEL)
            except h.CommerceError as exc:
                if "HTTP 404" not in str(exc):  # already gone: done
                    raise
            continue
        form = {"value_ids": p["value_ids"], "values": p["values"],
                **({"scale_id": p["scale_id"]} if p.get("scale_id") else {})}
        et.write("PUT", path, form, et.WRITE_SETTING, LABEL)
    uploaded, each = 0, []  # each photo's outcome, by name (the shop app, 2026-10-09: "done" didn't say which landed)
    for img in send.get("images") or []:
        if img.get("done_id"):
            each.append({"image": img["image"], "listing_image_id": img["done_id"], "uploaded": "earlier"})
            continue  # uploaded by an earlier try of this approval: never twice
        data = _image(img["image"])
        if hashlib.sha256(data).hexdigest() != img.get("sha256"):
            raise h.CommerceError(f"{img['image']} has changed since it was asked: decline this and ask again")
        name = _short_ref(img["image"]) if not img["image"].startswith("https://") else "printify-mockup.jpg"
        out = et.write("POST", f"/v3/application/shops/{shop}/listings/{lid}/images",
                       {k: img[k] for k in ("rank", "alt_text") if img.get(k)}, et.WRITE_SETTING, LABEL,
                       files={"image": (name, data, img.get("mime") or "image/jpeg")})
        img["done_id"] = (out or {}).get("listing_image_id") or True  # kept on the request (printify_pending saves it)
        uploaded += 1
        each.append({"image": img["image"], "listing_image_id": img["done_id"], "uploaded": "now"})
    # After the new photos are up: alt text on existing ones (Etsy's image upload re-saves an existing photo given its
    # listing_image_id), then deletions last, so the listing never has none. Both are safe to retry.
    # the re-save also carries the photo's current rank, so setting alt text can't move it (Etsy re-saves the photo
    # whole: a rank-only re-save blanked alt text, 2026-10-18)
    ranks = {p.get("listing_image_id"): p.get("rank") for p in _photos(lid)} if send.get("images_alt") else {}
    for a in send.get("images_alt") or []:
        rank = ranks.get(a["listing_image_id"])
        et.write("POST", f"/v3/application/shops/{shop}/listings/{lid}/images",
                 {"listing_image_id": a["listing_image_id"], "alt_text": a["alt_text"],
                  **({"rank": rank} if isinstance(rank, int) else {})}, et.WRITE_SETTING, LABEL)
    removed, backed_up = [], []
    if send.get("swap") and send.get("remove_images"):
        # an automatic swap (guards checked when it was asked): the replacement must be on the listing now, and the
        # old photo is saved first; either failing raises, so nothing is deleted and the request waits with the error
        photos = _photos(lid)
        new_ids = {i["listing_image_id"] for i in each}
        if not new_ids or not new_ids <= {p.get("listing_image_id") for p in photos}:
            raise h.CommerceError("the watermarked replacement isn't on the listing yet: nothing was deleted")
        backed_up = [_backup_photo(lid, int(iid), photos) for iid in send["remove_images"]]
    for iid in send.get("remove_images") or []:
        try:
            et.write("DELETE", f"/v3/application/shops/{shop}/listings/{lid}/images/{iid}", {}, et.WRITE_SETTING, LABEL)
        except h.CommerceError as exc:
            if "HTTP 404" not in str(exc):  # already gone: done
                raise
        removed.append(iid)
    if send.get("swap") and removed:
        kind = send.get("swap_kind") or "swap"
        _record_swap({"at": time.time(), "listing_id": lid, "removed": removed,
                      "backups": [b["listing_image_id"] for b in backed_up], "kind": kind,
                      **({"reason": send["delete_reason"]} if send.get("delete_reason") else {})})
        import clients
        clients._audit(f"etsy photo {'delete by a bot' if kind == 'bot' else 'swap'}", None,
                       listing=lid, removed=removed)
    # any photo change also tidies the ranks: closes a deletion's gap, repairs a listing an earlier upload left doubled
    reranked = _rerank(shop, lid, {i["listing_image_id"] for i in each if i["uploaded"] == "now"}) \
        if send.get("images") or send.get("images_alt") or removed else []
    wanted = payload.get("wanted") or {k: v for k, v in send.items() if k not in ("images", "remove_images", "images_alt")}
    with _lock:
        rows = applied()
        saved = rows.get(lid) or {}
        by_id = {p["property_id"]: p for p in saved.get("properties") or []}
        by_id.update({p["property_id"]: p for p in wanted.get("properties") or []})
        for p in wanted.get("properties") or []:
            if p.get("remove"):  # a removed attribute is never re-applied
                by_id.pop(p["property_id"], None)
        rows[lid] = {"fields": {**(saved.get("fields") or {}), **(wanted.get("fields") or {})},
                     "properties": list(by_id.values()), "title": payload.get("title"), "at": time.time()}
        _write(ATTRS_FILE, "listings", rows)
    # photos_landed == photos_asked means every photo asked for is on the listing ("earlier" ones landed on a previous
    # try of this approval); an upload that fails raises instead, and the request stays waiting with its error
    return {"listing_id": lid, "fields": sorted(fields), "attributes": [p["name"] for p in props], "photos": uploaded,
            "photos_asked": len(send.get("images") or []), "photos_landed": len(each), "photo_results": each,
            "photos_removed": removed, "alt_text_set": [a["listing_image_id"] for a in send.get("images_alt") or []],
            "photos_reranked": reranked,
            "note": "Set on Etsy. A Printify re-publish of this listing queues the details again for your approval."}


def _photos(lid: str) -> list[dict]:
    return [p for p in (et.get(f"/v3/application/listings/{lid}/images", None, False) or {}).get("results") or []
            if isinstance(p, dict) and p.get("listing_image_id")]


def _rerank(shop: str, lid: str, new_ids: set) -> list[dict]:
    """Etsy puts an upload at its rank but doesn't move the photo already there (live 2026-10-18: a hero at rank 1 left
    ranks [1, 1, 2, 3, 4, 5]). Re-number the listing's photos 1..n, a new photo ahead of an old one on the same rank,
    and re-save each one whose rank changed (Etsy's upload with listing_image_id and rank). Returns what moved.
    The re-save carries the photo's alt text: Etsy blanks it otherwise (live 2026-10-18, five photos lost theirs)."""
    photos = _photos(lid)
    if not all(isinstance(p.get("rank"), int) for p in photos):
        return []  # Etsy didn't say where each one is: leave them
    photos.sort(key=lambda p: (int(p.get("rank") or 99), p["listing_image_id"] not in new_ids))
    moved = []
    for rank, p in enumerate(photos, 1):
        if int(p.get("rank") or 0) != rank:
            et.write("POST", f"/v3/application/shops/{shop}/listings/{lid}/images",
                     {"listing_image_id": p["listing_image_id"], "rank": rank,
                      **({"alt_text": p["alt_text"]} if p.get("alt_text") else {})}, et.WRITE_SETTING, LABEL)
            moved.append({"listing_image_id": p["listing_image_id"], "rank": rank})
    return moved


@tool(name="listing.deactivate", category="etsy", doc="etsy/write.md")
def listing_deactivate(listing_id: str, reason: str) -> dict:
    """Take one of the shop's active Etsy listings off sale now (state inactive), e.g. one that fails a safety or
    delivery check. Runs at once, without an approval (it's a brake), behind its own switch "Let apps deactivate your
    Etsy listings"; at most ETSY_DEACTIVATE_DAILY_MAX a day (default 5); audited, and the owner gets a push each time.
    Etsy's Shop Manager re-activates it. Needs Etsy connected with listing edits.

    Args:
        listing_id: the listing's number (etsy.listings.list).
        reason: why, in a sentence (5-300 characters): it's in the owner's push and the audit log.

    Returns:
        {"listing_id", "title", "state": "inactive", "reason", "deactivated_today", "daily_max", "undo"}
    """
    lid = h.check_id(listing_id, "listing_id")
    why = " ".join(str(reason or "").split())
    if not 5 <= len(why) <= 300:
        raise h.CommerceError("reason: say why in 5-300 characters")
    h.require(et.SETTING, "Etsy")
    h.require(et.DEACTIVATE_SETTING, DEACTIVATE_LABEL)
    import clients
    who = clients.current_client.get() or "owner"
    with _lock:
        rows = [r for r in _read(DEACTIVATED_FILE, "deactivated", []) if isinstance(r, dict)]
        today = [r for r in rows if time.time() - float(r.get("at") or 0) < 86400]
        if len(today) >= deactivate_daily_max():
            raise h.CommerceError(f"{len(today)} listings were deactivated in the last 24 hours: the daily limit "
                                  f"({deactivate_daily_max()}, ETSY_DEACTIVATE_DAILY_MAX) is reached; ask the owner")
        x = _own_listing(lid)
        if x.get("state") != "active":
            raise h.CommerceError(f"that listing is {x.get('state')}, not active: nothing to deactivate")
        et.write("PATCH", f"/v3/application/shops/{et.shop_id()}/listings/{lid}", {"state": "inactive"},
                 et.DEACTIVATE_SETTING, DEACTIVATE_LABEL)
        title = h.trim(x.get("title"), 80) or "untitled"
        _write(DEACTIVATED_FILE, "deactivated",
               (rows + [{"at": time.time(), "listing_id": lid, "title": title, "by": who, "reason": why}])[-500:])
    clients._audit("etsy listing deactivated", None if who == "owner" else who, listing=lid, reason=why[:200])
    try:
        from tools.notify.send import send
        send(f'{who} took "{title}" off sale: {why}', title="Etsy listing deactivated", priority="high",
             tags=["warning"])
    except Exception:  # noqa: BLE001 - the push is a courtesy; the audit log has it either way
        pass
    return et.note({"listing_id": int(lid), "title": title, "state": "inactive", "reason": why,
                    "deactivated_today": len(today) + 1, "daily_max": deactivate_daily_max(),
                    "undo": "Etsy > Shop Manager > Listings > Inactive: select it > Activate"})
