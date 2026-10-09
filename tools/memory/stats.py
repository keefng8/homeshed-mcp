"""memory.stats. See ../../capabilities/memory/stats.md."""
from __future__ import annotations

from registry import tool


@tool(name="stats", category="memory", doc="memory/stats.md")
def stats() -> dict:
    """How much memory has been read and written. An app gets its own totals, by category (e.g. one per agent:
    "agent-design-agent"); the owner gets every app's totals and its share of all memory use. Counts only, never
    contents.

    Returns:
        app: {"app", "reads", "writes", "categories": {name: {"reads", "writes"}}, "since"};
        owner: {"reads", "writes", "apps": {name: {"reads", "writes", "share"}}, "since"}.
    """
    import clients
    import usage
    return usage.memory_stats(clients.current_client.get())
