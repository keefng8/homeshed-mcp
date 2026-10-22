"""agents.list / agents.send / agents.inbox. See ../../capabilities/agents/agents.md."""
from __future__ import annotations

import agents_mailbox
import clients
from registry import tool


@tool(name="list", category="agents", doc="agents/agents.md")
def list_agents(as_session: str = "") -> dict:
    """The agents you can message: id, label, last_seen, and how many unread messages you have from each.

    Returns:
        {"agents": [{"id", "label", "last_seen", "unread"}], "unread"}. "owner" reaches the owner's inbox.
    """
    return agents_mailbox.agents(clients.current_client.get(), as_session)


@tool(name="send", category="agents", doc="agents/agents.md")
def send(to: str, text: str, reply_to: str = "", as_session: str = "") -> dict:
    """Leave another agent a short message; it reads it with agents.inbox.

    Args:
        to: an agent id from agents.list, or "owner" (goes to the owner's inbox: Control Panel and phone).
        text: 1-2000 characters, plain words.
        reply_to: the id of the message you're answering, if any.
        as_session: only for a session on the owner's own token: your session's name (e.g. my-project), so you
            aren't shown as the owner and replies come to your own mailbox (read it with the same as_session).

    Returns:
        {"id", "to", "sent_at"}.
    """
    return agents_mailbox.send(clients.current_client.get(), to, text, reply_to, as_session)


@tool(name="inbox", category="agents", doc="agents/agents.md")
def inbox(unread_only: bool = True, limit: int = 20, mark_read: bool = True, for_client: str = "",
          as_session: str = "") -> dict:
    """Your own messages from other agents, newest first.

    Args:
        unread_only: only messages you haven't read yet.
        limit: at most this many (1-100).
        mark_read: mark the returned messages read.
        for_client: owner only: look at that agent's mailbox (read-only, marks nothing read).
        as_session: a session on the owner's token: read your own named mailbox (see agents.send).

    Returns:
        {"messages": [{"id", "from", "from_label", "text", "sent_at", "reply_to", "read"}], "unread"}.
    """
    return agents_mailbox.inbox(clients.current_client.get(), unread_only, limit, mark_read, for_client,
                                as_session)
