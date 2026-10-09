"""inbox.replies. See ../../capabilities/inbox/replies.md."""
from __future__ import annotations

import owner_inbox
from registry import tool


@tool(name="replies", category="inbox", doc="inbox/replies.md")
def replies(sender: str = "", limit: int = 10) -> dict:
    """Your messages to the owner and his replies, newest first.

    Args:
        sender: the name you sent with (empty: every message sent with your token).
        limit: how many messages, 1-50.

    Returns:
        {"messages": [{"id", "need", "text", "command", "at", "done", "replies": [{"text", "at"}]}], "waiting": int}
    """
    import clients
    rows = owner_inbox.for_sender(sender.strip(), client=clients.current_client.get() or "owner")
    limit = max(1, min(int(limit), 50))
    out = [{"id": i["id"], "need": i["need"], "text": i["text"], "command": i.get("command") or "", "at": i["at"],
            "done": bool(i.get("done_at")), "replies": i.get("replies") or []} for i in rows[:limit]]
    return {"messages": out, "waiting": sum(not m["done"] and not m["replies"] for m in out)}
