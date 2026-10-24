"""image.describe: a few short, visible details of an image's artwork, for writing about a design without seeing it.
See ../../capabilities/image/describe.md.

Asked for by the shop station (2026-10-24) for its design-facts sheet: its Promoter writes posts but can't see the
designs, so it invented details. This names only what is plainly visible (shapes, colours, parts, positions), never
the breed or species, a name, a fact or a guess. Same engine, caps and usage log as image.ip_check (Claude Haiku
through the llm relay); the image is sent for the one call and never kept.
"""
from __future__ import annotations

from registry import tool
from tools.image import ip_check as ipc

MAX_TOKENS = 1500
RULES = """You describe artwork for a writer who cannot see it. You get one image of a printed design (possibly on a
product mockup: describe the design, not the product or the background).

List short visible details, each 2 to 7 words, that someone looking at the design would agree with at once: shapes,
colours, parts and where they are (for example "long gold ears", "coral shapes behind the head", "three white
stars above"). Use plain colour words.

Never: name a breed, species, character, person, place or brand; give a fact, a mood, a story or a guess about what
the subject is or does; describe text or lettering; mention the garment, model, mockup or photo background.

Also give confidence: how sure you are that every detail is plainly visible, "high", "medium" or "low".
Answer only in JSON."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["details", "confidence"],
          "properties": {"details": {"type": "array", "items": {"type": "string"}},
                         "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}}
BANNED = ("breed", "species", "puppy", "kitten", "brand", "logo", "text", "letter", "word")


@tool(name="describe", category="image", doc="image/describe.md")
def describe(image_name: str = "", image_b64: str = "", count: int = 3) -> dict:
    """Short visible details of a design's artwork, e.g. "long gold ears", "coral shapes behind the head": only what is
    plainly there, never a breed, name, fact or guess. For writing about a design (posts, listings) without seeing it.
    Paid per call (Claude Haiku, about the cost of image.ip_check), inside the owner's caps.

    Args:
        image_name: a generated or stored image's name, "folder/name.png", or a Printify mockup address.
        image_b64: or the image itself, base64 (PNG, JPEG or WebP).
        count: how many details, 1 to 6 (default 3).

    Returns:
        {"ok": true, "details": [...], "confidence": "high"|"medium"|"low", "model", "cost_usd"}, or
        {"ok": false, "reason"}.
    """
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 6:
        return {"ok": False, "reason": "count must be 1 to 6"}
    if sum(1 for s in (image_name, image_b64) if s) != 1:
        return {"ok": False, "reason": "give exactly one of image_name or image_b64"}
    try:
        urls, size = ipc._images(ipc._load(image_name, image_b64), None)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)[:200]}
    parts = [{"type": "input_text", "text": f"Give {count} details. The image is {size[0]}x{size[1]} px."},
             {"type": "input_image", "image_url": urls[0]}]
    import clients
    from tools.llm import providers as llm
    client = clients.current_client.get() or "owner"
    cost, why = 0.0, ""
    for _ in range(2):
        try:
            llm.reserve(ipc.PROVIDER, client)
        except llm.LLMError as exc:
            return {"ok": False, "reason": f"couldn't run: {exc}"[:200]}
        try:
            out = llm.call(ipc.PROVIDER, ipc.MODEL, [{"role": "user", "content": parts}], RULES, MAX_TOKENS, None,
                           SCHEMA)
        except llm.LLMError as exc:
            llm.record(ipc.PROVIDER, client, ipc.MODEL, None, ok=False, error=str(exc))
            return {"ok": False, "reason": f"couldn't run: {exc}"[:200]}
        llm.record(ipc.PROVIDER, client, ipc.MODEL, out["usage"], ok=True)
        cost += out["usage"].get("cost_usd") or 0.0
        try:
            if out["incomplete"]:
                raise ValueError("the answer was cut off")
            d = ipc._parse(out["text"])
            details = [" ".join(str(x).split())[:60] for x in d.get("details") or [] if str(x).strip()]
            details = [x for x in details if not any(b in x.lower() for b in BANNED)][:count]
            if not details or d.get("confidence") not in ("high", "medium", "low"):
                raise ValueError("no usable details")
            break
        except (ValueError, TypeError) as exc:
            why = str(exc)
    else:
        return {"ok": False, "reason": f"the model's answer couldn't be used ({why})"}
    return {"ok": True, "details": details, "confidence": d["confidence"], "model": ipc.MODEL,
            "cost_usd": round(cost, 6)}
