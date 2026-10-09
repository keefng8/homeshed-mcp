"""observe.update. See ../../capabilities/observe/update.md."""
from __future__ import annotations

from registry import tool
from tools.observe import _store


@tool(name="update", category="observe", doc="observe/update.md")
def update(id: str, status: str, resolution: str = "") -> dict:
    """Change an observation's status, e.g. to "actioned" once its fix exists.

    Args:
        id: the observation id ("0003" or "3").
        status: open, actioned, declined, superseded or parked.
        resolution: what was done or why. It's appended to the file as a dated section, so
            the history stays readable.

    Returns:
        {"id", "status", "previous_status"}
    """
    return _store.update(obs_id=id, status=status, resolution=resolution)
