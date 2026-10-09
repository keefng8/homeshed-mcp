"""proxmox.vm.snapshot. See ../../capabilities/proxmox/changes.md."""
from __future__ import annotations

import proxmox_pending
from registry import tool
from tools.proxmox import _client as px


@tool(name="vm.snapshot", category="proxmox", doc="proxmox/changes.md")
def vm_snapshot(vmid: int, snapname: str, node: str = "", kind: str = "qemu", description: str = "",
                dry_run: bool = True) -> dict:
    """Take a snapshot of a VM or container (handy before a risky change). WRITE-RISK, never immediate: dry_run=true
    (default) checks only; dry_run=false queues it for the owner's approval. Never deletes or rolls back a snapshot.

    Args:
        vmid: its id (unique across the cluster).
        snapname: the snapshot's name: a letter first, then letters, digits, '_' or '-', 2-40 characters.
        node: the node it runs on. Empty (default): found cluster-wide from the vmid.
        kind: "qemu" (VM, default) or "lxc" (container).
        description: optional note stored with the snapshot (up to 200 characters).
        dry_run: true (default): report what would happen. false: queue it for the owner.

    Returns:
        dry run: {"dry_run": true, "change", "checks"}; queued: {"pending": true, "id", "change", "expires_in_h", "how"}

    Raises:
        ProxmoxError: bad arguments, the guest not found or locked, missing settings, Proxmox unreachable.
        PendingError: too many changes already waiting.
    """
    px.check_snapname(snapname)
    node = px.resolve_node(px.check_vmid(vmid), node)
    rec = px.check_action_record({"op": "snapshot", "node": node, "kind": kind, "vmid": vmid, "snapname": snapname,
                                  "description": description})
    checks = px.preflight(rec)
    if dry_run:
        return {"dry_run": True, "change": px.describe(rec), "checks": checks}
    return proxmox_pending.add(rec, px.describe(rec), checks)
