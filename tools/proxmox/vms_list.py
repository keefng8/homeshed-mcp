"""proxmox.vms.list. See ../../capabilities/proxmox/read.md."""
from __future__ import annotations

from registry import tool
from tools.proxmox import _client as px

MAX_ROWS = 100


@tool(name="vms.list", category="proxmox", doc="proxmox/read.md")
def vms_list(node: str = "", kind: str = "all") -> dict:
    """List virtual machines (qemu) and containers (lxc) across the whole cluster (one /cluster/resources call), read-only: vmid, name, node, status, CPU, memory, uptime.

    Args:
        node: only this node's guests. Empty (default) = every node in the cluster.
        kind: "all" (default), "qemu" (VMs) or "lxc" (containers).

    Returns:
        {"guests": [{"vmid", "name", "kind", "node", "status", "template", "cpus", "cpu_pct", "mem_gib",
          "maxmem_gib", "uptime_s"}], "summary": {"total", "running", "stopped"}, "truncated": bool}
        At most 100 rows, sorted by vmid.

    Raises:
        ProxmoxError: a bad node or kind, missing settings, Proxmox unreachable or refusing.
    """
    if node:
        node = px.check_node(node)
    if kind != "all":
        px.check_kind(kind)
    rows = [r for r in px.guests(node) if kind == "all" or r.get("type") == kind]
    rows.sort(key=lambda r: r.get("vmid") or 0)
    guests = [{"vmid": r.get("vmid"), "name": r.get("name"), "kind": r.get("type"), "node": r.get("node"),
               "status": r.get("status"), "template": bool(r.get("template")), "cpus": r.get("maxcpu"),
               "cpu_pct": px.pct(r.get("cpu")), "mem_gib": px.gib(r.get("mem")), "maxmem_gib": px.gib(r.get("maxmem")),
               "uptime_s": r.get("uptime")} for r in rows[:MAX_ROWS]]
    running = sum(1 for r in rows if r.get("status") == "running")
    return {"guests": guests, "summary": {"total": len(rows), "running": running, "stopped": len(rows) - running},
            "truncated": len(rows) > MAX_ROWS}
