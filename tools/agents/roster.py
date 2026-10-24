"""agents.roster / agents.roster_set: who's who among the agents. See ../../capabilities/agents/roster.md."""
from __future__ import annotations

import agents_roster
import clients
from registry import tool


@tool(name="roster", category="agents", doc="agents/roster.md")
def roster() -> dict:
    """Every agent's name (as ListAgents or agents.list shows it), role and how to reach it, owner-confirmed first.

    Returns:
        {"agents": [{"name", "role", "reach", "how", "confirmed", "by", "updated", "gone", "proposed"?}], "count"}.
    """
    try:
        return agents_roster.roster()
    except agents_roster.RosterError as exc:
        return {"error": str(exc)}


@tool(name="roster_set", category="agents", doc="agents/roster.md")
def roster_set(name: str, role: str, reach: str = "session", as_session: str = "") -> dict:
    """Add yourself, or another agent, to the roster; the owner confirms it in the Control Panel.

    Args:
        name: the agent's name exactly as ListAgents (or agents.list) shows it, e.g. "Environment setup".
        role: what it does, in plain words, e.g. "library builder (Homeshed-addon)". Up to 200 characters.
        reach: session (SendMessage, on the owner's PC), cloud (a cloud session) or mailbox (agents.send only).
        as_session: your own session's name, recorded as who set the row.

    Returns:
        {"name", "status"}. A row the owner confirmed keeps their wording; your change waits beside it.
    """
    try:
        return agents_roster.set_row(name, role, reach, as_session or clients.current_client.get() or "owner token")
    except agents_roster.RosterError as exc:
        return {"error": str(exc)}
