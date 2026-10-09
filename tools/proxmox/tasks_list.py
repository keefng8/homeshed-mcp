"""proxmox.tasks.list. See ../../capabilities/proxmox/read.md."""
from __future__ import annotations

from registry import tool
from tools.proxmox import _client as px


@tool(name="tasks.list", category="proxmox", doc="proxmox/read.md")
def tasks_list(node: str, limit: int = 20, errors_only: bool = False, vmid: int = 0) -> dict:
    """Recent Proxmox tasks on a node (read-only): starts, stops, backups, snapshots, and how they ended. Use it to
    see how an approved change went (its upid).

    Args:
        node: the node's name.
        limit: how many, newest first, 1-50. Default 20.
        errors_only: only tasks that failed.
        vmid: only this guest's tasks. 0 (default) = all.

    Returns:
        {"node", "tasks": [{"upid", "type", "id", "user", "status", "started", "ended"}]}
        status is "running" while it runs, "OK" when it worked, otherwise Proxmox's error text. Times are UTC.

    Raises:
        ProxmoxError: a bad argument, missing settings, Proxmox unreachable or refusing.
    """
    node = px.check_node(node)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise px.ProxmoxError("limit must be from 1 to 50")
    params: dict = {"limit": limit}
    if errors_only:
        params["errors"] = 1
    if vmid:
        params["vmid"] = px.check_vmid(vmid)
    rows = px.get(f"/nodes/{node}/tasks", params) or []
    tasks = [{"upid": t.get("upid"), "type": t.get("type"), "id": t.get("id"), "user": t.get("user"),
              "status": t.get("status") or ("running" if not t.get("endtime") else None),
              "started": px.iso(t.get("starttime")), "ended": px.iso(t.get("endtime"))}
             for t in rows[:limit] if isinstance(t, dict)]
    return {"node": node, "tasks": tasks}
