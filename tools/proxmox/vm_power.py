"""proxmox.vm.power. See ../../capabilities/proxmox/changes.md."""
from __future__ import annotations

import proxmox_pending
from registry import tool
from tools.proxmox import _client as px


@tool(name="vm.power", category="proxmox", doc="proxmox/changes.md")
def vm_power(vmid: int, action: str, node: str = "", kind: str = "qemu", dry_run: bool = True) -> dict:
    """Start, stop, shut down or reboot a VM or container. WRITE-RISK, and never immediate: dry_run=true (default)
    only checks and reports; dry_run=false queues the change for the owner's approval (proxmox_pending.py). Nothing
    changes on Proxmox until the owner approves it.

    Args:
        vmid: its id (unique across the cluster).
        action: "start", "stop" (hard power-off), "shutdown" (asks the guest OS) or "reboot".
        node: the node it runs on. Empty (default): found cluster-wide from the vmid.
        kind: "qemu" (VM, default) or "lxc" (container).
        dry_run: true (default): report what would happen. false: queue it for the owner.

    Returns:
        dry run: {"dry_run": true, "change", "checks": {name, status_now, note?, warning?}}
        queued:  {"pending": true, "id", "change", "expires_in_h", "how"}

    Raises:
        ProxmoxError: bad arguments, the guest not found or locked, missing settings, Proxmox unreachable.
        PendingError: too many changes already waiting.
    """
    px.check_action(action)
    node = px.resolve_node(px.check_vmid(vmid), node)
    rec = px.check_action_record({"op": "power", "node": node, "kind": kind, "vmid": vmid, "action": action})
    checks = px.preflight(rec)
    if dry_run:
        return {"dry_run": True, "change": px.describe(rec), "checks": checks}
    return proxmox_pending.add(rec, px.describe(rec), checks)
