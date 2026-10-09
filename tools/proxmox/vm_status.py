"""proxmox.vm.status. See ../../capabilities/proxmox/read.md."""
from __future__ import annotations

from registry import tool
from tools.proxmox import _client as px


@tool(name="vm.status", category="proxmox", doc="proxmox/read.md")
def vm_status(vmid: int, node: str = "", kind: str = "qemu") -> dict:
    """One VM's or container's live status (read-only).

    Args:
        vmid: its id, e.g. 101 (unique across the cluster).
        node: the node it runs on. Empty (default): found cluster-wide from the vmid.
        kind: "qemu" (VM, default) or "lxc" (container).

    Returns:
        {"node", "kind", "vmid", "name", "status", "qmp_status", "lock", "uptime_s", "cpus", "cpu_pct",
         "mem_gib", "maxmem_gib", "disk_gib", "maxdisk_gib", "netin_bytes", "netout_bytes", "ha_state"}

    Raises:
        ProxmoxError: a bad argument, the guest not found (HTTP 500 from Proxmox), missing settings, unreachable.
    """
    kind, vmid = px.check_kind(kind), px.check_vmid(vmid)
    node = px.resolve_node(vmid, node)
    s = px.current_status(node, kind, vmid)
    ha = s.get("ha") if isinstance(s.get("ha"), dict) else {}
    return {"node": node, "kind": kind, "vmid": vmid, "name": s.get("name"), "status": s.get("status"),
            "qmp_status": s.get("qmpstatus"), "lock": s.get("lock"), "uptime_s": s.get("uptime"),
            "cpus": s.get("cpus"), "cpu_pct": px.pct(s.get("cpu")), "mem_gib": px.gib(s.get("mem")),
            "maxmem_gib": px.gib(s.get("maxmem")), "disk_gib": px.gib(s.get("disk")),
            "maxdisk_gib": px.gib(s.get("maxdisk")), "netin_bytes": s.get("netin"), "netout_bytes": s.get("netout"),
            "ha_state": ha.get("state")}
