"""image.ip_check. See ../../capabilities/image/ip_check.md.

A vision check before anything is listed (2026-10-22): does an image carry a brand, a known character, a real person
or unsafe content, and, for a lifestyle hero photo, is a child visible anywhere or is something covering the print.
The engine is Claude Haiku through the llm relay, so its switch, caps, monthly limit and usage log all apply. Fail
closed: anything that stops the check returns ok false, which callers treat as NOT clear. The image is sent for the
one call and never kept; only the verdict is returned and the usage log has no text.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import re

from registry import tool

PROVIDER, MODEL = "anthropic", "claude-haiku-5-5"
PURPOSES = ("upload", "design", "hero")
MAX_SIDE = 1568           # Claude's own resize point: larger images only cost more
MAX_B64_BYTES = 10 * 1024 * 1024
CROP_MARGIN = 0.15        # the print crop keeps this share of the box's size around it
MAX_TOKENS = 2000         # Haiku 5.5 thinks first and that counts here: 600 cut the JSON short (seen live 2026-10-22)

# The fixed rules (the system prompt): the same text on every call, so the relay's prompt cache serves it at 0.1x.
RULES = """You check product images for an online print shop before they are listed. You get one image (and, for a
lifestyle photo, a second image that is a close crop of the printed design on the garment). Look carefully at the whole
image, including blurred backgrounds, reflections, signs, posters and small details.

For each question first write a short note (under 15 words) saying what you see and where, then answer found true or
false to match the note.

brand: a recognisable logo, wordmark, trademark, team or club badge, or a famous product design (on the print, on
clothing, on objects or signs anywhere in the image). Plain generic text or shapes are not a brand, and nor is a
faint word repeated across the whole image: that is a watermark (see watermark), never a brand.
character: a cartoon, film, TV, game, anime, comic or toy character, a mascot, or fan art that is close enough to be
recognised as one. Generic animals, people or creatures drawn in a common style are not characters.
person: a real, identifiable person's face as the subject (a photo of a real individual).
celebrity: a famous person or public figure (an actor, musician, athlete, politician, influencer, royal), in a photo
or as a drawing or likeness close enough to be recognised. An ordinary private person is not a celebrity.
watermark: a mark you can actually see and point to: a stock agency's name or logo, faint text or a pattern repeated
across the image, a photographer's signature, or a copyright line. A photo that merely looks polished or professional
but carries no visible mark is false.
artwork: someone else's identifiable finished work: a known painting or artwork, a film, game, book or album cover, a
poster or print with a credit, signature or title on it, stock art with its source shown, a screenshot (app or game
interface, browser, phone screen), or a photo of a screen. A polished illustration or design by itself is NOT artwork,
whatever its style (pop art, retro, cartoon); nor is a drawing that is clearly the customer's own.
unsafe: sexual content, nudity or partial nudity, hate symbols, graphic violence, drugs, or weapons as the focus.
child: anyone who looks under 18, a child-sized person, or a baby, anywhere in the image, including small, partly
hidden or blurred figures in the background.
print_covered: anything lying over any part of the printed design on the garment, hiding even a small part of it:
drawstrings, cords, a scarf or its fringe, hair, a hand or arm, a bag strap, a fold or crease, jewellery, or another
object. Check every edge of the print, especially the top edge near the neck and the sides. Something hanging near
the print without overlapping it is false. If no print is visible, answer false and say so in the note.
garment: only when the request names the listing's garment. The garment the model is wearing is clearly a different
type (a t-shirt where a hoodie is named: no hood and no front pocket; a crew-neck tee where a sweatshirt is named:
short sleeves). Colour, fit and print don't count here. If no garment is named, answer false.

Also give confidence: how sure you are of the whole answer, from 0 to 1. Be strict: if you are unsure whether
something is there, answer found true and lower the confidence.

Reply in the JSON shape you are given."""

FLAGS = ("brand", "character", "person", "celebrity", "watermark", "artwork", "unsafe", "child", "print_covered",
         "garment")
# hero only (2026-10-23): the garment the listing sells, so a scene showing another type is caught (image.compare
# checks the print and colour, not the garment). An unknown value is ignored, as if none was given.
GARMENTS = {"tee": "a t-shirt (short sleeves, no hood)", "hoodie": "a hoodie (a hood, usually a front pocket)",
            "sweatshirt": "a crew-neck sweatshirt (long sleeves, no hood)",
            "kids_tee": "a children's t-shirt (short sleeves, no hood)"}
_FLAG = {"type": "object", "properties": {"note": {"type": "string"}, "found": {"type": "boolean"}},
         "required": ["note", "found"], "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {**{f: _FLAG for f in FLAGS}, "confidence": {"type": "number"}},
          "required": [*FLAGS, "confidence"], "additionalProperties": False}

# Which flags count, worst first, per purpose. A lifestyle hero has a model in it by design (an AI person), so
# "person" doesn't count there; a child or a covered print does. Copyright (2026-10-23): someone else's photo or art
# (celebrity, watermark, artwork) counts on an upload; a design can't carry a watermark or a celebrity ("artwork" would
# fire on every design); a hero carries the shop's own tiled watermark, so "watermark" doesn't count there.
# "person" stays last: the verdict is "person" only when nothing worse is there, so a caller may allow it.
ORDER = {"upload": ("unsafe", "celebrity", "watermark", "artwork", "character", "brand", "person"),
         "design": ("unsafe", "celebrity", "watermark", "character", "brand", "person"),
         "hero": ("unsafe", "child", "print_covered", "garment", "celebrity", "character", "brand")}
VERDICT = {"print_covered": "covered"}


def _fail(reason: str) -> dict:
    return {"ok": False, "verdict": None, "reason": reason[:200]}


def _load(image_name: str, image_b64: str) -> bytes:
    if image_name:
        from tools.commerce._http import CommerceError
        from tools.image.refs import image_bytes
        try:
            return image_bytes(image_name.strip())
        except CommerceError as exc:
            raise ValueError(str(exc)) from None
    raw = image_b64.split(",", 1)[1] if image_b64.startswith("data:") else image_b64
    if len(raw) > MAX_B64_BYTES * 4 // 3 + 4:
        raise ValueError("image_b64 is over 10 MB")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("image_b64 isn't valid base64") from None


def _jpeg(im, side: int = MAX_SIDE) -> str:
    im = im.convert("RGB")
    im.thumbnail((side, side))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def _images(data: bytes, print_box) -> tuple[list[str], tuple[int, int]]:
    """The image (and the print crop) as JPEG data URLs, no larger than Claude would look at anyway."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001 - not an image
        raise ValueError("that isn't an image (PNG, JPEG or WebP)") from None
    urls = [_jpeg(im)]
    if print_box:
        if not (isinstance(print_box, list) and len(print_box) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in print_box)):
            raise ValueError("print_box is [x0, y0, x1, y1] in the image's pixels (as image.compare gives it)")
        x0, y0, x1, y1 = (int(v) for v in print_box)
        if not (0 <= x0 < x1 <= im.width and 0 <= y0 < y1 <= im.height):
            raise ValueError(f"print_box {print_box} isn't inside the {im.width}x{im.height} image")
        mx, my = int((x1 - x0) * CROP_MARGIN), int((y1 - y0) * CROP_MARGIN)
        box = (max(0, x0 - mx), max(0, y0 - my), min(im.width, x1 + mx), min(im.height, y1 + my))
        urls.append(_jpeg(im.crop(box), 1024))
    return urls, im.size


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("the model didn't answer in JSON")
    d = json.loads(m.group(0))
    if not isinstance(d, dict):
        raise ValueError("the model's answer isn't a JSON object")
    return d


@tool(name="ip_check", category="image", doc="image/ip_check.md")
def ip_check(image_name: str = "", image_b64: str = "", purpose: str = "upload",
             print_box: list[int] | None = None, garment: str = "") -> dict:
    """Vision check before listing: a brand, a known character, a real person or unsafe content in an image, and for a
    lifestyle hero photo a visible child (blurred background included) or something covering the print. Claude Haiku
    through the llm relay (about $0.0005 a call). Fail closed: ok false means the
    check didn't run, so treat the image as NOT clear.

    Args:
        image_name: a saved image's name (saved_as, an image.store name, "promo/<name>.png"). Or:
        image_b64: the image itself (PNG, JPEG or WebP, base64, up to 10 MB).
        purpose: "upload" (a buyer's photo, default), "design" (a generated design) or "hero" (a lifestyle listing
            photo with a model in it: a person doesn't count, a child or a covered print does).
        print_box: hero only, optional: [x0, y0, x1, y1], the print's box in the image's pixels from image.compare;
            a close crop of it is checked too, for strings, a scarf, hair or a hand over the print.
        garment: hero only, optional: "tee", "hoodie", "sweatshirt" or "kids_tee", the garment the listing sells;
            a scene showing another type gives verdict "garment". Any other value is ignored.

    Returns:
        {"ok": true, "verdict": "clear"|"brand"|"character"|"person"|"celebrity"|"watermark"|"artwork"|"unsafe"|"child"|
         "covered"|"garment", "confidence": 0-1,
         "found": "what and where", "flags": {name: note} for every flag that counts here, "model", "cost_usd"},
        or {"ok": false, "verdict": null, "reason"}.
    """
    if purpose not in PURPOSES:
        return _fail(f"purpose is one of {list(PURPOSES)}")
    if sum(1 for s in (image_name, image_b64) if s) != 1:
        return _fail("give exactly one of image_name or image_b64")
    if print_box and purpose != "hero":
        return _fail('print_box goes with purpose "hero"')
    wear = GARMENTS.get(str(garment or "").strip().lower(), "") if purpose == "hero" else ""
    try:
        urls, size = _images(_load(image_name, image_b64), print_box)
    except ValueError as exc:
        return _fail(str(exc))
    ask = (f'Purpose: {purpose}. The image is {size[0]}x{size[1]} px.'
           + (" The second image is a close crop of the print." if len(urls) > 1 else "")
           + (f" The listing's garment is {wear}." if wear else "")
           + " Check it against every rule.")
    order = tuple(k for k in ORDER[purpose] if k != "garment" or wear)
    parts = [{"type": "input_text", "text": ask}] + [{"type": "input_image", "image_url": u} for u in urls]
    import clients
    from tools.llm import providers as llm
    client = clients.current_client.get() or "owner"
    cost, why = 0.0, ""
    for _ in range(2):  # one more try when the answer can't be read (each try is counted and capped as usual)
        try:
            llm.reserve(PROVIDER, client)
        except llm.LLMError as exc:
            return _fail(f"the check couldn't run: {exc}")
        try:
            out = llm.call(PROVIDER, MODEL, [{"role": "user", "content": parts}], RULES, MAX_TOKENS, None, SCHEMA)
        except llm.LLMError as exc:
            llm.record(PROVIDER, client, MODEL, None, ok=False, error=str(exc))
            return _fail(f"the check couldn't run: {exc}")
        llm.record(PROVIDER, client, MODEL, out["usage"], ok=True)
        cost += out["usage"].get("cost_usd") or 0.0
        try:
            if out["incomplete"]:
                raise ValueError("the answer was cut off")
            d = _parse(out["text"])
            flags = {k: str((d.get(k) or {}).get("note") or "")[:120] for k in order
                     if (d.get(k) or {}).get("found") is True}
            conf = max(0.0, min(1.0, float(d.get("confidence"))))
            break
        except (ValueError, TypeError) as exc:
            why = str(exc)
    else:
        return _fail(f"the model's answer couldn't be read ({why})")
    worst = next((k for k in order if k in flags), None)
    return {"ok": True, "verdict": VERDICT.get(worst, worst) if worst else "clear", "confidence": round(conf, 2),
            "found": flags.get(worst, "") if worst else "", "flags": flags, "model": MODEL,
            "cost_usd": round(cost, 6)}
