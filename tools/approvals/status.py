"""approvals.status. See ../../capabilities/approvals/status.md."""
from __future__ import annotations

import approvals
from registry import tool


@tool(name="status", category="approvals", doc="approvals/status.md")
def status(request_id: str) -> dict:
    """What happened to one request that waited for the owner: a shared-memory write, a Proxmox change, a shop request
    or a message to the owner. Check this rather than assuming it still waits. An app sees only its own requests.

    Args:
        request_id: the id the request was queued under (8 letters and digits, e.g. "3fa9c021").

    Returns:
        {"id", "queue": memory|proxmox|commerce|inbox, "kind"?, "status": waiting|approved|declined|expired|withdrawn|
         parked, "summary", "asked_at", "decided_at"?, "decided_by"?: "owner"|"bot:<name>"|"auto"}, or {"id",
         "status": "unknown"} for an id that isn't yours or isn't kept any more.
    """
    import clients
    return approvals.status(request_id, clients.current_client.get())
