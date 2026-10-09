"""memory.recall. See ../../capabilities/memory/recall.md."""
from __future__ import annotations

import httpx

from registry import tool
from tools.memory.capture import MemoryError, _config, _local

MAX_LIMIT = 100  # memory-core answers HTTP 400 above 100 (found by the Research and Development session, 2026-09-29)
MAX_CHARS_LIMIT = 20000


def shorten(messages: list[dict], max_chars: int) -> list[dict]:
    """Cut each message's content to max_chars (0 = full text) and mark the ones cut. Keeps the
    per-request memory check cheap (task protocol, RULE-D-PROJECTS-010): one fact can run to
    thousands of characters."""
    if not max_chars:
        return messages
    out = []
    for m in messages:
        text = m.get("content")
        if isinstance(text, str) and len(text) > max_chars:
            m = {**m, "content": text[:max_chars].rstrip() + "…", "truncated": True}
        out.append(m)
    return out


def check_max_chars(max_chars: int) -> None:
    if not 0 <= max_chars <= MAX_CHARS_LIMIT:
        raise MemoryError(f"max_chars must be between 0 (full text) and {MAX_CHARS_LIMIT}")


@tool(name="recall", category="memory", doc="memory/recall.md")
def recall(session_id: str, limit: int = 20, max_chars: int = 0, scope: str = "project", project_dir: str = "") -> dict:
    """Read back messages previously saved with memory.capture for a given session. Raw L0
    conversation history, not semantic search — returns exactly what was written, in order.

    Args:
        session_id: the session_id used when capturing (memory.capture).
        limit: max messages to return, 1-100.
        max_chars: cut each message's content to this many characters (ending "…", marked
            "truncated": true). 0 (the default) returns the full text.
        scope: "project" (default) reads this project's own memory; "global" reads the shared platform
            memory (homelab-wide facts every project may need), whichever client asks.
        project_dir: the caller's working folder; picks that project's own memory (projects.py).

    Returns:
        {"messages": [{"id", "role", "content", "timestamp"}], "total": int}

    Raises:
        MemoryError: not configured, backend unreachable, or the query was rejected.
    """
    if not session_id or not session_id.strip():
        raise MemoryError("session_id must be non-empty")
    if not 1 <= limit <= MAX_LIMIT:
        raise MemoryError(f"limit must be between 1 and {MAX_LIMIT}")
    check_max_chars(max_chars)

    cfg = _config(scope, project_dir)
    if cfg.get("local"):  # the built-in store (memory_local.py): the same shapes, newest first
        trimmed = _local(lambda m: m.query(cfg["agent_id"], session_id, limit))
        return {"messages": shorten(trimmed, max_chars), "total": len(trimmed)}

    try:
        response = httpx.post(
            f"{cfg['base_url'].rstrip('/')}/v3/conversation/query",
            headers={
                "Authorization": f"Bearer {cfg['bearer']}",
                "x-tdai-service-id": cfg["service_id"],
                "x-tdai-user-key": cfg["user_key"],
            },
            json={
                "team_id": cfg["team_id"],
                "user_id": cfg["user_id"],
                "agent_id": cfg["agent_id"],
                "session_id": session_id,
                "limit": limit,
            },
            timeout=15,
        )
    except httpx.TimeoutException:
        raise MemoryError("memory-core timed out") from None
    except httpx.RequestError:
        raise MemoryError("memory-core is unavailable") from None

    if response.status_code != 200:
        raise MemoryError(f"memory-core returned HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError:
        raise MemoryError("memory-core returned an unparseable response") from None

    if data.get("code") != 0:
        raise MemoryError(f"memory-core rejected the query: {data.get('message', 'unknown error')}")

    messages = (data.get("data") or {}).get("messages") or []
    trimmed = [
        {
            "id": m.get("id"),
            "role": m.get("role"),
            "content": m.get("content"),
            "timestamp": m.get("timestamp"),
        }
        for m in messages
    ]
    return {"messages": shorten(trimmed, max_chars), "total": len(trimmed)}
