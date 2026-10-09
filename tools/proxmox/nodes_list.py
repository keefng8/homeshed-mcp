"""proxmox.nodes.list. See ../../capabilities/proxmox/read.md."""
from __future__ import annotations

from registry import tool
from tools.proxmox import _client as px


@tool(name="nodes.list", category="proxmox", doc="proxmox/read.md")
def nodes_list() -> dict:
    """List every node in the Proxmox cluster (read-only): name, online/offline, CPU and memory use, uptime. One
    API token covers the cluster; the request goes to the configured entry node (PROXMOX_HOST).

    Returns:
        {"host", "cluster": {"name", "quorate", "nodes"} | None (a single node has none), "nodes": [{"node", "status", "cpu_pct", "cpus", "mem_used_gib", "mem_total_gib", "uptime_s"}]}

    Raises:
        ProxmoxError: PROXMOX_TOKEN_ID / _TOKEN_SECRET not set (named, never shown), Proxmox unreachable,
            the token refused, or a TLS problem (the message says which setting fixes it).
    """
    rows = px.get("/nodes") or []
    nodes = [{"node": n.get("node"), "status": n.get("status"), "cpu_pct": px.pct(n.get("cpu")),
              "cpus": n.get("maxcpu"), "mem_used_gib": px.gib(n.get("mem")), "mem_total_gib": px.gib(n.get("maxmem")),
              "uptime_s": n.get("uptime")} for n in rows if isinstance(n, dict)]
    return {"host": px.config()["base"], "cluster": _cluster(), "nodes": sorted(nodes, key=lambda n: str(n["node"]))}


def _cluster() -> dict | None:
    """The cluster's name and quorum, best effort: a standalone node, or a token without Sys.Audit on /, has none."""
    try:
        rows = px.get("/cluster/status") or []
    except px.ProxmoxError:
        return None
    c = next((r for r in rows if isinstance(r, dict) and r.get("type") == "cluster"), None)
    return {"name": c.get("name"), "quorate": bool(c.get("quorate")), "nodes": c.get("nodes")} if c else None
