"""observe.list. See ../../capabilities/observe/list.md."""
from __future__ import annotations

from registry import tool
from tools.observe import _store


@tool(name="list", category="observe", doc="observe/list.md")
def list_observations(status: str = "open", rule: str = "", limit: int = 50, action_class: str = "") -> dict:
    """List observations, oldest first.

    Args:
        status: one of open, actioned, declined, superseded, parked, or "all".
        rule: only observations tagged with this rule id (empty = any).
        limit: max entries returned; "count" is always the full match count.
        action_class: only this kind of slip, e.g. "shell.grep-command" (empty = any).

    Returns:
        {"count", "observations": [{"id", "title", "status", "rule", "class", "area", "date", "source", "stops",
        "last_seen"}]}. stops: guard stops of the class recorded by the daily self-review.
    """
    return _store.list_all(status=status, rule=rule, limit=limit, action_class=action_class)
