"""proxmox.health. See ../../capabilities/proxmox/monitor.md."""
from __future__ import annotations

from registry import tool


@tool(name="health", category="proxmox", doc="proxmox/monitor.md")
def health() -> dict:
    """The whole Proxmox cluster at a glance (read-only): quorum, each node's CPU, RAM and system disk, the storage
    pools, how many VMs and containers run, and the alarms that are active now (the same as the Control Panel's
    Proxmox tab and the owner's phone alerts).

    Returns:
        {"cluster": {name, quorate, nodes_online, nodes}, "nodes": [{node, status, cpu_pct, ram_pct, disk_pct,
         disk_free_gb}], "storage": [{storage, node, used_pct, free_gb}], "guests": {running, stopped, templates},
         "alarms": [{id, severity, title, since}], "checked_at"}

    Raises:
        ProxmoxError: missing settings, Proxmox unreachable or refusing.
    """
    import proxmox_monitor as pm
    s = pm.snapshot()

    def gb(n):
        return round(n / 1e9, 1) if isinstance(n, (int, float)) else None
    guests = [g for g in s.get("guests") or [] if not g.get("template")]
    return {"cluster": s.get("cluster"),
            "nodes": [{"node": n.get("node"), "status": n.get("status"), "cpu_pct": n.get("cpu_pct"),
                       "ram_pct": pm._pct(n.get("mem_used"), n.get("mem_total")),
                       "disk_pct": pm._pct(n.get("root_used"), n.get("root_total")),
                       "disk_free_gb": gb(n.get("root_avail"))} for n in s.get("nodes") or []],
            "storage": [{"storage": x.get("storage"), "node": x.get("node") or "shared",
                         "used_pct": pm._pct(x.get("used"), x.get("total")),
                         "free_gb": gb((x.get("total") or 0) - (x.get("used") or 0)) if x.get("total") else None}
                        for x in s.get("storage") or [] if x.get("status") == "available"],
            "guests": {"running": sum(g.get("status") == "running" for g in guests),
                       "stopped": sum(g.get("status") != "running" for g in guests),
                       "templates": sum(bool(g.get("template")) for g in s.get("guests") or [])},
            "alarms": [{k: a.get(k) for k in ("id", "severity", "title", "since")}
                       for a in (s.get("alarms") or {}).get("active") or []],
            "checked_at": s.get("at")}
