"""etsy.listing.update and etsy.listing.deactivate (2026-10-08, the shop's director: both behind one listings_w
reconnect). See ../../capabilities/etsy/write.md.

etsy.listing.update sets the details of one of the shop's listings that Printify doesn't manage: its category
(taxonomy), materials, production partners, delivery profile, and attributes (Etsy "properties": colour, occasion,
recipient, holiday...). It changes a public listing, so a call only checks it and queues it (printify_pending.py,
action "etsy"); the owner approves it in the Control Panel. Title, description, tags and prices go through Printify
(printify.product.update_live): set here, Printify's next publish would undo them.

The director's rule: details are set after a listing's last publish, and a re-publish never leaves them undone. What
was last set on each listing is kept (ATTRS_FILE). An approved printify.product.update_live of that listing queues
them again straight after (queue_reapply), for the owner to approve once Printify has finished publishing.

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
from urllib.parse import quote, urlsplit

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
FOLDER_REF = re.compile(r"^([A-Za-z0-9][A-Za-z0-9 _-]{0,39})/([A-Za-z0-9][A-Za-z0-9._-]{0,100}\.(?:png|jpg|jpeg))$")
NAME_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.(?:png|jpg|jpeg)$")
_lock = threading.Lock()


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, min(100, int(os.environ.get(name) or default)))
    except ValueError:
        return default


def edit_daily_max() -> int:
    return _env_int("ETSY_EDIT_DAILY_MAX", 20)


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


def _check_fields(taxonomy_id, materials, production_partner_ids, shipping_profile_id) -> dict:
    fields: dict = {}
    for name, value in (("taxonomy_id", taxonomy_id), ("shipping_profile_id", shipping_profile_id)):
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
        if not vals or not all(isinstance(v, str) and v.strip() for v in vals):
            raise h.CommerceError(f'"{name}": give a value, or a list of values')
        key = str(name).strip().lower()
        prop = next((p for p in props if key in (str(p.get("name")).lower(), str(p.get("display_name")).lower())), None)
        if prop is None:
            raise h.CommerceError(h.trim(f'"{name}" isn\'t an attribute of category {taxonomy_id}; it has: '
                                         + ", ".join(names), 500))
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
    """One image's bytes: a Printify mockup (on Printify's image host only), an image saved in a folder of the image
    store under its own name ("listing/krc-staffy-artwork-2400.png"), one the tool server keeps (mockup copies, paid
    designs), or a generated one by its name."""
    from tools.image import providers as prov
    from tools.image.generate import base_url
    from tools.printify.mockup import MOCKUP_HOSTS
    ref = (ref or "").strip()
    headers: dict = {}
    if ref.startswith("https://"):
        u = urlsplit(ref)
        if u.hostname not in MOCKUP_HOSTS or u.username or u.password or len(ref) > 1000:
            raise h.CommerceError(f"{h.trim(ref, 80)}: only Printify mockup addresses (printify.product.get lists them)")
        url, headers = ref, {"User-Agent": "HomeShed-mcp-server"}
    elif m := FOLDER_REF.match(ref):
        url = f"{base_url()}/image/folder-files/{quote(m[1])}/{m[2]}"
    elif NAME_REF.match(ref):
        kept = prov.output_dir() / ref
        if kept.is_file():
            if kept.stat().st_size > MAX_IMAGE_BYTES:
                raise h.CommerceError(f"{ref} is over {MAX_IMAGE_BYTES // 2**20} MB")
            return kept.read_bytes()
        url = f"{base_url()}/image/files/{ref}"
    else:
        raise h.CommerceError(f'{h.trim(ref, 80)!r}: give an image name, "folder/name.png" or a Printify mockup address')
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=False, headers=headers) as r:
            if r.status_code == 404:
                raise h.CommerceError(f"there's no image {ref}")
            if r.status_code != 200:
                raise h.CommerceError(f"couldn't get {ref} (HTTP {r.status_code})")
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    raise h.CommerceError(f"{ref} is over {MAX_IMAGE_BYTES // 2**20} MB")
    except httpx.HTTPError as exc:
        raise h.CommerceError(f"couldn't get {ref} ({type(exc).__name__})") from None
    return data


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
    for k, label in (("taxonomy_id", "category"), ("shipping_profile_id", "delivery profile")):
        if k in fields:
            parts.append(f"{label} {x[k]} -> {fields[k]}" if x.get(k) else f"{label} {fields[k]}")
    if "materials" in fields:
        parts.append("materials: " + (", ".join(fields["materials"]) or "none"))
    if "production_partner_ids" in fields:
        parts.append("production partners: " + (", ".join(map(str, fields["production_partner_ids"])) or "none"))
    parts += [f"{p['name']}: {', '.join(p['values'])}" for p in props]
    return "; ".join(parts)


def _queue(lid: str, title: str, summary: str, send: dict, wanted: dict, reapply: bool,
           thumbs: list | None = None) -> dict:
    import printify_pending
    checks = {"listing_id": lid, "fields": sorted(send["fields"]), "attributes": [p["name"] for p in send["properties"]],
              "images": len(send.get("images") or []), "reapply": reapply,
              "edited_last_24h": len(printify_pending.done_today("etsy")), "daily_max": edit_daily_max()}
    payload = {"listing_id": lid, "title": title, "send": send, "wanted": wanted, **({"thumbs": thumbs} if thumbs else {})}
    try:
        return printify_pending.add(f"etsy-{lid}", h.trim(summary, 300), checks, action="etsy", payload=payload)
    except printify_pending.PendingError as exc:
        raise h.CommerceError(str(exc)) from None


@tool(name="listing.update", category="etsy", doc="etsy/write.md")
def listing_update(listing_id: str, taxonomy_id: int = 0, materials: list[str] | None = None,
                   production_partner_ids: list[int] | None = None, shipping_profile_id: int = 0,
                   properties: dict | None = None, reapply: bool = False, images: list[dict] | None = None) -> dict:
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
        reapply: set again everything this tool last set on the listing (after a re-publish undid it); the other
            arguments are then ignored. Photos aren't re-applied (a Printify update never re-sends photos).
        images: 1-5 photos to add, [{"image": ref, "alt_text"?: up to 500 characters, "rank"?: 1-10}], where ref is
            "folder/name.png" (saved in the image store under its own name, e.g. "listing/krc-staffy-artwork-2400.png"),
            an image name, or a Printify mockup address. JPEG, PNG or GIF up to 10 MB; the listing holds at most 10.
            Each photo is fingerprinted now, and checked again before it's uploaded on approval.

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
        fields = _check_fields(taxonomy_id, materials, production_partner_ids, shipping_profile_id)
        if properties is not None and not isinstance(properties, dict):
            raise h.CommerceError('properties must be {"attribute name": "value" or ["values"]}')
        if not fields and not properties and not images:
            raise h.CommerceError("nothing to change: give a category, materials, production partners, a delivery "
                                  "profile, attributes or images")
        props = []
    new_images = _check_images(images) if images and not reapply else []
    x = _own_listing(lid)
    thumbs = []
    if new_images:
        have_n = (et.get(f"/v3/application/listings/{lid}/images") or {}).get("count") or 0
        if have_n + len(new_images) > MAX_LISTING_IMAGES:
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
    now = {k: x.get(k) for k in fields}
    if isinstance(now.get("production_partner_ids"), list):
        now["production_partner_ids"] = sorted(now["production_partner_ids"])
    send_fields = {k: v for k, v in fields.items() if now.get(k) != v}
    cur = et.get(f"/v3/application/shops/{et.shop_id()}/listings/{lid}/properties", oauth=False) or {}
    have = {p.get("property_id"): sorted(p.get("value_ids") or []) for p in cur.get("results") or [] if isinstance(p, dict)}
    send_props = [p for p in props if have.get(p["property_id"]) != sorted(p["value_ids"])]
    if not send_fields and not send_props and not new_images:
        return {"listing_id": lid, "pending": False, "note": "already as asked: nothing queued"}
    title = h.trim(x.get("title"), 60) or "untitled"
    said = "; ".join(s for s in (_said(x, send_fields, send_props),
                                 ("photos: " + ", ".join(_short_ref(i["image"]) for i in new_images)) if new_images else "")
                     if s)
    summary = f'{"Re-apply" if reapply else "Set"} Etsy details on "{title}": {said}'
    return _queue(lid, title, summary, {"fields": send_fields, "properties": send_props, "images": new_images},
                  {"fields": fields, "properties": props}, reapply, thumbs)


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
        form = {"value_ids": p["value_ids"], "values": p["values"],
                **({"scale_id": p["scale_id"]} if p.get("scale_id") else {})}
        et.write("PUT", f"/v3/application/shops/{shop}/listings/{lid}/properties/{p['property_id']}", form,
                 et.WRITE_SETTING, LABEL)
    uploaded = 0
    for img in send.get("images") or []:
        if img.get("done_id"):
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
    wanted = payload.get("wanted") or {k: v for k, v in send.items() if k != "images"}
    with _lock:
        rows = applied()
        saved = rows.get(lid) or {}
        by_id = {p["property_id"]: p for p in saved.get("properties") or []}
        by_id.update({p["property_id"]: p for p in wanted.get("properties") or []})
        rows[lid] = {"fields": {**(saved.get("fields") or {}), **(wanted.get("fields") or {})},
                     "properties": list(by_id.values()), "title": payload.get("title"), "at": time.time()}
        _write(ATTRS_FILE, "listings", rows)
    return {"listing_id": lid, "fields": sorted(fields), "attributes": [p["name"] for p in props], "photos": uploaded,
            "note": "Set on Etsy. A Printify re-publish of this listing queues the details again for your approval."}


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
