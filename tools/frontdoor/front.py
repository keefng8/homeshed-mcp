"""tools.*: the front door (../../frontdoor.py). Five tools that find, describe and call every other tool, for apps whose
tool list is the front door only. See ../../capabilities/tools/front-door.md."""
from __future__ import annotations

import re

from registry import tool

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by can do for from get how i in into is it me my of on or the this to up "
                  "what when which with you your".split())


def _caller() -> str | None:
    import clients
    return clients.current_client.get()


def _usable(client: str | None) -> list[dict]:
    """The manifests of the tools this caller may call and the front door shows (its grant, then Pro's tool set)."""
    import clients
    import frontdoor
    from manifest import load_manifests
    out = []
    for m in load_manifests():
        tid = m["id"]
        if not frontdoor.visible(client, tid):
            continue
        if client is not None and not clients.is_allowed(client, tid, m.get("risk", "read"), m.get("category", "")):
            continue
        out.append(m)
    return out


def _line(m: dict) -> str:
    text = str(m.get("description") or "").strip()
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if len(first) <= 160 else first[:157].rstrip() + "..."


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _score(m: dict, words: list[str], phrase: str) -> float:
    tid, name = m["id"].lower(), str(m.get("name") or "").lower()
    aliases = [str(a).lower() for a in m.get("aliases") or []]
    desc = str(m.get("description") or "").lower()
    id_words, name_words = set(_WORD.findall(tid)), set(_WORD.findall(name))
    alias_words, desc_words = set(_words(" ".join(aliases))), set(_words(desc))
    score = 0.0
    if phrase == tid:
        score += 100
    if any(phrase and (phrase in a or a in phrase) for a in aliases):
        score += 8
    for w in words:
        stems = {w, w.rstrip("s")}
        if stems & id_words:
            score += 4
        if stems & name_words:
            score += 3
        if stems & alias_words:
            score += 2.5
        if stems & desc_words:
            score += 1
    return score / (1 + 0.15 * max(0, len(words) - 3))


@tool(name="find", category="tools", doc="tools/front-door.md")
def find(task: str, limit: int = 8) -> dict:
    """Find the tools for a job, best first. Start here: then tools.describe one to see its inputs, and tools.call it.

    Args:
        task: what you want to do, in plain words, e.g. "list my Etsy orders" or "restart a container". A tool id
            works too.
        limit: how many to return, 1-20 (default 8).

    Returns:
        {"tools": [{"id", "does", "risk": read|write}], "next": what to do with them}. Only tools you may call.
        No match: "tools" is empty and "next" points at tools.groups.
    """
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task is what you want to do, in plain words")
    limit = max(1, min(20, int(limit)))
    import frontdoor
    phrase, client = task.strip().lower(), _caller()
    words = _words(phrase)
    ranked = sorted(((s + frontdoor.boost(client, m["id"]), m) for m in _usable(client)
                     if (s := _score(m, words, phrase)) > 0), key=lambda sm: (-sm[0], sm[1]["id"]))
    found = [{"id": m["id"], "does": _line(m), "risk": m.get("risk", "read")} for _, m in ranked[:limit]]
    nxt = ("tools.describe(name) shows a tool's inputs; tools.call(name, arguments) runs it." if found else
           "Nothing matched: tools.groups() lists what's here by group.")
    return {"tools": found, "next": nxt}


@tool(name="groups", category="tools", doc="tools/front-door.md")
def groups(group: str = "") -> dict:
    """What's here, by group, or the tools in one group.

    Args:
        group: a group name from the list (e.g. "etsy"), to see its tools; leave empty for the list of groups.

    Returns:
        No group: {"groups": [{"group", "tools": count, "e.g.": a few tool ids}]}.
        A group: {"group", "tools": [{"id", "does", "risk"}]}. Only tools you may call.
    """
    usable = _usable(_caller())
    if group:
        g = str(group).strip().lower()
        items = [{"id": m["id"], "does": _line(m), "risk": m.get("risk", "read")}
                 for m in sorted(usable, key=lambda m: m["id"]) if str(m.get("category", "")).lower() == g]
        if not items:
            raise ValueError(f"no group {group!r} among the tools you may use; tools.groups() lists them")
        return {"group": g, "tools": items}
    by: dict[str, list[str]] = {}
    for m in usable:
        by.setdefault(str(m.get("category") or "other"), []).append(m["id"])
    return {"groups": [{"group": g, "tools": len(ids), "e.g.": sorted(ids)[:3]} for g, ids in sorted(by.items())]}


@tool(name="describe", category="tools", doc="tools/front-door.md")
def describe(name: str) -> dict:
    """One tool in full: what it does and its inputs, exactly as the full tool list would show it.

    Args:
        name: the tool id, e.g. "etsy.orders.list" (from tools.find or tools.groups).

    Returns:
        {"name", "description", "inputSchema", "outputSchema"?, "risk"}. Then run it with tools.call(name, arguments).
    """
    import clients
    import server
    from registry import tool_meta
    client = _caller()
    risk, category = tool_meta(str(name))
    info = server.mcp._tool_manager.get_tool(str(name))
    import frontdoor
    if info is None or str(name) in frontdoor.FRONT_TOOLS or (
            client is not None and not clients.is_allowed(client, str(name), risk, category)):
        raise ValueError(f"no tool {name!r} you may use; tools.find(task) finds one")
    out = {"name": info.name, "description": info.description, "inputSchema": info.parameters, "risk": risk}
    if info.output_schema:
        out["outputSchema"] = info.output_schema
    return out


@tool(name="result", category="tools", doc="tools/front-door.md")
def result(result_id: str, fields: list[str] | None = None, query: str = "", offset: int = 0,
           limit: int = 50) -> dict:
    """Read back a result tools.call stored with to_file=true, a page at a time.

    Args:
        result_id: the id tools.call returned ("r_..."), kept 24 hours.
        fields: optional paths to keep, e.g. ["name", "status"]; when paging a list they name each item's fields.
        query: optional plain text: only list items containing it (any field, any case).
        offset: where in the list to start (default 0).
        limit: items per page, 1-500 (default 50).

    Returns:
        A list result: {"items", "total", "offset", "next_offset" (null at the end), "from"?: which key held the list}.
        Otherwise {"result": the stored value} (or the trimmed fields).
    """
    import frontdoor
    return frontdoor.read(result_id, _caller(), fields, query, offset, limit)


@tool(name="call", category="tools", doc="tools/front-door.md")
def call(name: str, arguments: dict | None = None, fields: list[str] | None = None, to_file: bool = False) -> dict:
    """Run any tool by name, with the same checks as calling it directly (your access, approvals, spend limits).

    Args:
        name: the tool id, e.g. "etsy.orders.list".
        arguments: its inputs as an object (tools.describe shows them), e.g. {"limit": 5}.
        fields: optional: keep only these parts of the reply, e.g. ["items[].title", "count"]. The reply says how
            many keys were dropped ("_dropped") and any path it didn't find ("_missing"). Ids and status are always kept.
        to_file: true to keep a big reply on the server: you get its id, size, outline and a preview, and read it
            with tools.result.

    Returns:
        The tool's own reply (trimmed to fields, or as a stored-result handle with to_file).
    """
    raise RuntimeError("tools.call runs in the server's dispatcher (server.py); it can't be called directly")
