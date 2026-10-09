"""inbox.amend and inbox.withdraw. See ../../capabilities/inbox/amend.md."""
from __future__ import annotations

import owner_inbox
from registry import tool


def _client() -> str:
    import clients
    return clients.current_client.get() or "owner"


@tool(name="amend", category="inbox", doc="inbox/amend.md")
def amend(message_id: str, sender: str = "", text: str = "", command: str = "", need: str = "") -> dict:
    """Correct your own message to the owner before he closes it: a wrong command, a typo, a changed ask. Fix it here
    rather than sending a second message; the panel shows it as edited and keeps the old wording.

    Args:
        message_id: the id inbox.send returned (inbox.replies lists them).
        sender: the name you sent with.
        text, command, need: only the parts to change; leave the others empty.

    Returns:
        {"id", "amended": true, "need", "edits": how many times it has been changed}.
    """
    item = owner_inbox.amend(message_id.strip(), sender.strip(), _client(), text=text or None,
                             command=command or None, need=need or None)
    return {"id": item["id"], "amended": True, "need": item["need"], "edits": len(item.get("edits") or [])}


@tool(name="withdraw", category="inbox", doc="inbox/amend.md")
def withdraw(message_id: str, sender: str = "") -> dict:
    """Take back your own message to the owner (sent by mistake, or no longer needed). It leaves his list at once.

    Returns:
        {"withdrawn": id}.
    """
    return owner_inbox.withdraw(message_id.strip(), sender.strip(), _client())
