"""The front door (free core, to-do #145, 2026-10-27; design: ../.claude/front-door-design.md).

A client can see the whole tool list, or only five tools that find, describe and call the rest (`tool_view`); results
can be trimmed to named fields or stored server-side and read back in slices; pictures can go out as MCP image content
instead of base64 text inside the JSON (`images`). server.py calls this module from its one dispatcher, so a call
through the front door gets exactly the checks a direct call gets.

Per-client choices live in clients.json (clients.set_view). Unset means today's behaviour ("full", "inline"), so no
existing app changes; clients created after this shipped start on the front door with images as content. HomeShed Pro
narrows what the front door shows (toolset_filter); without Pro the front door shows the client's whole grant.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

from paths import data_path

from clients import FRONT_DOOR as _FRONT_DOOR

FRONT_TOOLS = tuple(sorted(_FRONT_DOOR))
VIEWS = ("full", "front_door")
IMAGE_MODES = ("inline", "content")
NEW_CLIENT_VIEW, NEW_CLIENT_IMAGES = "front_door", "content"
MAX_IMAGES = 4  # image blocks per reply; more stay out with a note (a carousel can hold 10)
RESULTS_DIR = Path(os.environ.get("FRONT_DOOR_RESULTS_DIR") or data_path("usage/results"))
RESULT_TTL_S = 24 * 3600
RESULTS_PER_CLIENT = 20
RESULT_MAX_BYTES = 5 * 1024 * 1024
PREVIEW_CHARS = 1500
RESULT_ID_RE = re.compile(r"^r_[A-Za-z0-9_-]{16}$")
_lock = threading.Lock()

# The bug sync's key patterns (tools/bugs/known.py), without its generic "any 40+ character run" rule: a stored result
# is the caller's own data, and that rule would hide every sha256, listing id list and URL path in it.
_SECRET_RES = [
    re.compile(r"\b(?:hlc_|nvapi-|gh[pousr]_|github_pat_|sk-|xox[abprs]-|mav_)[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
]
_ASSIGNED = re.compile(r"\b((?:[A-Z][A-Z0-9_]*)?(?:TOKEN|KEY|SECRET|PASSWORD|BEARER))(\s*[=:]\s*)[\"']?[^\s\"']{6,}[\"']?")

# Set by HomeShed Pro (homeshed_pro registers it at load): (client, tool_id) -> bool, narrowing what find/groups show.
toolset_filter: Callable[[str | None, str], bool] | None = None
# Also Pro's: (client, tool_id) -> a score to add in tools.find, from the tools that app actually uses.
rank_boost: Callable[[str | None, str], float] | None = None


# Also Pro's (approval cards, MCP Apps): extra tool _meta by tool id, e.g. {"ui": {"resourceUri": "ui://...",
# "visibility": ["app"]}}, and ui:// resources {uri: (name, html function)} that server.py registers at start.
tool_ui_meta: dict[str, dict] = {}
ui_resources: dict[str, tuple[str, Callable[[], str]]] = {}
UI_MIME = "text/html;profile=mcp-app"


def model_may_see(tool_id: str) -> bool:
    """False for a tool only an app's card may call (MCP Apps visibility ["app"]): find and groups never offer it."""
    vis = ((tool_ui_meta.get(tool_id) or {}).get("ui") or {}).get("visibility")
    return not isinstance(vis, list) or "model" in vis


def boost(client: str | None, tool_id: str) -> float:
    try:
        return float(rank_boost(client, tool_id)) if rank_boost is not None else 0.0
    except Exception:  # noqa: BLE001 - a broken Pro hook never breaks find
        return 0.0


class FrontDoorError(ValueError):
    """A front-door call that can't go ahead; the message says what to send instead."""


# --- per-client choices ------------------------------------------------------------------------------------------------

def _client(name: str | None) -> dict:
    if name is None:
        return {}
    import clients
    return clients._load()["clients"].get(name) or {}


def view(client: str | None) -> str:
    """"full" or "front_door". The owner's sessions always get the full list: Claude Code's tool search does this job."""
    v = _client(client).get("tool_view")
    return v if v in VIEWS else "full"


def image_mode(client: str | None) -> str:
    """"inline" or "content". The owner stays inline: the Control Panel reads image.status's JSON over MCP."""
    v = _client(client).get("images")
    return v if v in IMAGE_MODES else "inline"


def visible(client: str | None, tool_id: str) -> bool:
    """Whether find/groups show this tool to the client (the grant is checked separately, as for any call)."""
    if tool_id in FRONT_TOOLS or not model_may_see(tool_id):
        return False
    try:
        return toolset_filter is None or bool(toolset_filter(client, tool_id))
    except Exception:  # noqa: BLE001 - a broken Pro filter never hides the free tools
        return True


# --- fields ------------------------------------------------------------------------------------------------------------

_KEEP_ALWAYS = ("id", "request_id", "status", "queued", "approval", "waiting_for", "error", "ok", "reason")


def _parse(path: str) -> list[str]:
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_$-]+(\[\])?(\.[A-Za-z0-9_$-]+(\[\])?)*", path):
        raise FrontDoorError(f"field path {path!r} isn't valid: use names and dots, with [] after a list, e.g. "
                             "items[].name")
    out = []
    for part in path.split("."):
        out += [part[:-2], "[]"] if part.endswith("[]") else [part]
    return out


def _pick(value, steps: list[str]):
    """The part of value one path names, keeping its shape; (found, picked)."""
    if not steps:
        return True, value
    head, rest = steps[0], steps[1:]
    if head == "[]":
        if not isinstance(value, list):
            return False, None
        picked = [_pick(v, rest) for v in value]
        return any(f for f, _ in picked), [p for f, p in picked if f] if rest else value
    if isinstance(value, dict) and head in value:
        found, sub = _pick(value[head], rest)
        return found, {head: sub} if found else None
    return False, None


def _merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _merge(out[k], v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [_merge(x, y) for x, y in zip(a, b)]
    return b


def _count_keys(value) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_count_keys(v) for v in value.values())
    if isinstance(value, list):
        return sum(_count_keys(v) for v in value)
    return 0


def trim(result, fields: list[str]):
    """Only the named fields of a dict result, plus "_dropped" (how many keys went) and "_missing" (paths not found).
    A queued or approval reply keeps its id and status whatever was asked (R&D, 27 Oct): the caller needs them."""
    if not fields:
        return result
    if not isinstance(fields, list) or len(fields) > 50:
        raise FrontDoorError("fields is a list of up to 50 paths, e.g. [\"items[].name\", \"total\"]")
    if not isinstance(result, dict):
        result = {"result": result}
    paths = [(f, _parse(f)) for f in fields]
    kept: dict = {k: result[k] for k in _KEEP_ALWAYS if k in result}
    missing = []
    for raw, steps in paths:
        found, part = _pick(result, steps)
        if found:
            kept = _merge(kept, part)
        else:
            missing.append(raw)
    kept["_dropped"] = max(0, _count_keys(result) - _count_keys(kept))
    if missing:
        kept["_missing"] = missing
    return kept


# --- stored results ----------------------------------------------------------------------------------------------------

def scrub(value):
    """value with key-shaped secrets in its strings replaced by [hidden] (strings, not the JSON text: a pattern that
    ate a closing quote would break the stored file)."""
    if isinstance(value, str):
        value = _ASSIGNED.sub(lambda m: f"{m.group(1)}{m.group(2)}[hidden]", value)
        for rx in _SECRET_RES:
            value = rx.sub("[hidden]", value)
        return value
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def _owner_key(client: str | None) -> str:
    return client or "owner"


def _index_file() -> Path:
    return RESULTS_DIR / "index.json"


def _load_index() -> dict:
    try:
        data = json.loads(_index_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_index(index: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _index_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(index), encoding="utf-8")
    os.replace(tmp, _index_file())


def _shape(value, depth: int = 0):
    """A short outline of a result: keys and list lengths, two levels deep."""
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in list(value.items())[:30]} if depth < 2 else f"{{{len(value)} keys}}"
    if isinstance(value, list):
        return f"list of {len(value)}"
    return type(value).__name__


def store(result, client: str | None, tool_id: str) -> dict:
    """Keep a result server-side for tools.result and reply with a handle, its size, outline and a preview."""
    text = json.dumps(scrub(result), ensure_ascii=False, default=str)
    raw = text.encode("utf-8")
    if len(raw) > RESULT_MAX_BYTES:
        raise FrontDoorError(f"that result is {len(raw) // 1024} KB, over the {RESULT_MAX_BYTES // 1048576} MB a stored "
                             "result may be: ask for fewer items, or use fields")
    now, who = time.time(), _owner_key(client)
    rid = "r_" + secrets.token_urlsafe(12)[:16]
    evicted = []
    with _lock:
        index = {k: v for k, v in _load_index().items() if now - float(v.get("at") or 0) < RESULT_TTL_S}
        mine = sorted((k for k, v in index.items() if v.get("client") == who), key=lambda k: index[k]["at"])
        while len(mine) >= RESULTS_PER_CLIENT:
            evicted.append(mine.pop(0))
        for k in [k for k in _load_index() if k not in index] + evicted:
            index.pop(k, None)
            (RESULTS_DIR / f"{k}.json").unlink(missing_ok=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / f"{rid}.json").write_bytes(raw)
        index[rid] = {"client": who, "tool": tool_id, "at": now, "bytes": len(raw)}
        _save_index(index)
    out = {"result_id": rid, "tool": tool_id, "bytes": len(raw), "shape": _shape(result),
           "preview": text[:PREVIEW_CHARS], "expires_in_h": RESULT_TTL_S // 3600,
           "read_with": "tools.result(result_id, fields?, query?, offset?, limit?)"}
    if evicted:
        out["evicted"] = f"{len(evicted)} older stored result(s) removed: {RESULTS_PER_CLIENT} are kept per app"
    return out


def read(result_id: str, client: str | None, fields: list[str] | None = None, query: str = "", offset: int = 0,
         limit: int = 50) -> dict:
    """A stored result back. Its main list (the result itself, or its longest list) comes in pages of `limit` from
    `offset`, filtered by `query` (plain text, any field); `fields` then name each item's fields. A result with no list,
    or one small enough to fit a page with no query, comes back whole (trimmed to fields)."""
    if not isinstance(result_id, str) or not RESULT_ID_RE.match(result_id):
        raise FrontDoorError("result_id is the id tools.call returned with to_file (r_ and 16 characters)")
    entry = _load_index().get(result_id)
    if not entry or entry.get("client") != _owner_key(client) or time.time() - float(entry.get("at") or 0) >= RESULT_TTL_S:
        raise FrontDoorError("no stored result with that id for this app (they're kept 24 hours)")
    try:
        value = json.loads((RESULTS_DIR / f"{result_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FrontDoorError("that stored result can't be read any more; run the call again") from None
    offset, limit = max(0, int(offset)), max(1, min(500, int(limit)))
    items_key, items = None, value if isinstance(value, list) else None
    if isinstance(value, dict):
        lists = [(k, v) for k, v in value.items() if isinstance(v, list)]
        if lists:
            items_key, items = max(lists, key=lambda kv: len(kv[1]))
    if items is None or (not query and not offset and len(items) <= limit):
        return trim(value, fields) if fields else {"result": value}
    if query:
        q = query.lower()
        items = [i for i in items if q in json.dumps(i, ensure_ascii=False, default=str).lower()]
    page = items[offset:offset + limit]
    dropped = 0
    if fields:
        page = [trim(i, fields) for i in page]
        for i in page:
            dropped += i.pop("_dropped", 0)
    out = {"items": page, "total": len(items), "offset": offset,
           "next_offset": offset + limit if offset + limit < len(items) else None}
    if items_key:
        out["from"] = items_key
    if fields:
        out["_dropped"] = dropped
    return out


# --- images ------------------------------------------------------------------------------------------------------------

def take_images(result) -> tuple[object, list[tuple[str, str]]]:
    """(the result without its base64 images, [(base64, mime)]): a top-level image_b64 and those in a top-level list
    of dicts, up to MAX_IMAGES; any more are left out with a note to fetch them one at a time."""
    if not isinstance(result, dict) or result.get("image_raw") is True:  # the caller asked for the bytes (image.fetch raw)
        return result, []
    images: list[tuple[str, str]] = []
    out = dict(result)
    if isinstance(out.get("image_b64"), str) and out["image_b64"]:
        images.append((out.pop("image_b64"), str(out.get("image_mime") or "image/png")))
        out["image"] = "sent as an image block"
    for key, value in result.items():
        if not isinstance(value, list) or not any(isinstance(v, dict) and isinstance(v.get("image_b64"), str)
                                                  for v in value):
            continue
        items, skipped = [], 0
        for v in value:
            if isinstance(v, dict) and isinstance(v.get("image_b64"), str) and v["image_b64"]:
                v = dict(v)
                b64 = v.pop("image_b64")
                if len(images) < MAX_IMAGES:
                    images.append((b64, str(v.get("image_mime") or "image/png")))
                    v["image"] = f"image block {len(images)}"
                else:
                    skipped += 1
                    v["image"] = "left out: fetch it on its own"
            items.append(v)
        out[key] = items
        if skipped:
            out["_images_left_out"] = skipped
    return out, images
