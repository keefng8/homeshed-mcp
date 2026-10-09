"""proxmox.storage.list. See ../../capabilities/proxmox/read.md."""
from __future__ import annotations

from registry import tool
from tools.proxmox import _client as px


@tool(name="storage.list", category="proxmox", doc="proxmox/read.md")
def storage_list(node: str = "") -> dict:
    """List storage pools and how full they are (read-only).

    Args:
        node: only this node's storage. Empty (default) = every node in the cluster.

    Returns:
        {"storage": [{"storage", "node", "type", "status", "content", "shared", "used_gib", "total_gib",
          "used_pct"}]}

    Raises:
        ProxmoxError: a bad node, missing settings, Proxmox unreachable or refusing.
    """
    if node:
        node = px.check_node(node)
    rows = px.get("/cluster/resources", {"type": "storage"}) or []
    out = []
    for r in rows:
        if not isinstance(r, dict) or (node and r.get("node") != node):
            continue
        used, total = r.get("disk"), r.get("maxdisk")
        out.append({"storage": r.get("storage"), "node": r.get("node"), "type": r.get("plugintype"),
                    "status": r.get("status"), "content": r.get("content"), "shared": bool(r.get("shared")),
                    "used_gib": px.gib(used), "total_gib": px.gib(total),
                    "used_pct": round(used * 100 / total, 1) if isinstance(used, (int, float)) and total else None})
    return {"storage": sorted(out, key=lambda s: (str(s["node"]), str(s["storage"])))}
