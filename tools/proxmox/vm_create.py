"""proxmox.vm.create. See ../../capabilities/proxmox/changes.md."""
from __future__ import annotations

import proxmox_pending
from registry import tool
from tools.proxmox import _client as px


@tool(name="vm.create", category="proxmox", doc="proxmox/changes.md")
def vm_create(node: str, vmid: int, kind: str, options: dict, dry_run: bool = True) -> dict:
    """Create a new VM (qemu) or container (lxc). WRITE-RISK, never immediate: dry_run=true (default) validates and
    checks the vmid is free; dry_run=false queues it for the owner's approval. Never replaces an existing guest.

    Args:
        node: the cluster node to create it on (required: proxmox.nodes.list shows them).
        vmid: a free id (create refuses one in use; Proxmox's next free id shows in its UI).
        kind: "qemu" (VM) or "lxc" (container).
        options: Proxmox's own create parameters, flat, e.g. qemu {"name": "web", "memory": 2048, "cores": 2,
            "net0": "virtio,bridge=vmbr0", "scsi0": "local-lvm:32", "ide2": "local:iso/debian.iso,media=cdrom"};
            lxc {"hostname": "web", "ostemplate": "local:vztmpl/...", "rootfs": "local-lvm:8", "memory": 1024,
            "net0": "name=eth0,bridge=vmbr0,ip=dhcp", "ssh-public-keys": "ssh-ed25519 ..."}. Refused: anything
            that overwrites, destroys or restores (force, archive, unique, delete, purge) or holds a secret
            (password, cipassword, anything named *secret*/*token*): set a root password in the Proxmox UI.
        dry_run: true (default): report what would happen. false: queue it for the owner.

    Returns:
        dry run: {"dry_run": true, "change", "options", "checks": {"vmid_free": true}}
        queued:  {"pending": true, "id", "change", "expires_in_h", "how"}

    Raises:
        ProxmoxError: bad arguments or options, the vmid in use, missing settings, Proxmox unreachable.
        PendingError: too many changes already waiting.
    """
    rec = px.check_action_record({"op": "create", "node": node, "kind": kind, "vmid": vmid, "options": options})
    checks = px.preflight(rec)
    if dry_run:
        return {"dry_run": True, "change": px.describe(rec), "options": rec["options"], "checks": checks}
    return proxmox_pending.add(rec, px.describe(rec), checks)
