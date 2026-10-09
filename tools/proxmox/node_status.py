"""proxmox.node.status. See ../../capabilities/proxmox/read.md."""
from __future__ import annotations

from registry import tool
from tools.proxmox import _client as px


@tool(name="node.status", category="proxmox", doc="proxmox/read.md")
def node_status(node: str) -> dict:
    """One Proxmox node's health (read-only): CPU, load, memory, swap, root disk, versions, uptime.

    Args:
        node: the node's name, as proxmox.nodes.list shows it (e.g. "pve").

    Returns:
        {"node", "uptime_s", "cpu_pct", "cpus", "cpu_model", "load_avg", "memory": {used_gib, total_gib},
         "swap": {...}, "rootfs": {...}, "pve_version", "kernel"}

    Raises:
        ProxmoxError: a bad node name, missing settings, Proxmox unreachable or refusing.
    """
    node = px.check_node(node)
    s = px.get(f"/nodes/{node}/status") or {}

    def used_total(d):
        d = d if isinstance(d, dict) else {}
        return {"used_gib": px.gib(d.get("used")), "total_gib": px.gib(d.get("total"))}

    cpu = s.get("cpuinfo") if isinstance(s.get("cpuinfo"), dict) else {}
    return {"node": node, "uptime_s": s.get("uptime"), "cpu_pct": px.pct(s.get("cpu")), "cpus": cpu.get("cpus"),
            "cpu_model": cpu.get("model"), "load_avg": s.get("loadavg"), "memory": used_total(s.get("memory")),
            "swap": used_total(s.get("swap")), "rootfs": used_total(s.get("rootfs")),
            "pve_version": s.get("pveversion"),
            "kernel": (s.get("current-kernel") or {}).get("release") if isinstance(s.get("current-kernel"), dict)
            else s.get("kversion")}
