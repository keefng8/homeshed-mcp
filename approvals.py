"""One answer to "what happened to my request?" across the four queues that wait for the owner (front door, 2026-10-27).

The queues: memory (memory_pending.py, apps' writes to the shared memory), proxmox (proxmox_pending.py), commerce
(printify_pending.py: Printify, Etsy and Pinterest requests) and inbox (owner_inbox.py, agents' messages). Each keeps
its own file; this only reads them. GET /approvals/<id> (owner) and approvals.status (an app, its own requests only)
answer with status().

Where a decision is found, per queue:
- memory, proxmox: a decided request leaves the queue file, so the decision comes from the client audit trail
  (clients.AUDIT_FILE). Only the owner decides these (owner-only admin routes), so decided_by is "owner". A Proxmox
  change with no decision after its TTL is "expired" (it's dropped unlogged); a memory write has no TTL.
- commerce: its decisions file (printify_pending.history). The note says who approved: "approved in <app> (<id>,
  <by>)" is an app's approval, whose "by" is "bot:<name>", an automatic actor ("auto...") or the owner inside the
  app; no "approved in" is the owner's tap in the Control Panel. Expired, withdrawn and parked have no decider.
- inbox: a message the owner marked done is "approved" by the owner; one withdrawn by its sender is gone, so unknown.

Rules this module keeps:
- Read-only, and never raises for a missing or unreadable file: that source is skipped.
- A client sees only the requests it made; the owner (client None) sees all.
"""
from __future__ import annotations

import json
import re
import time

ID_RE = re.compile(r"^[0-9a-f]{8}$")
SUMMARY_MAX = 200
_VIA_RE = re.compile(r"^approved in \S+ \([^,]*, ([^;)]*)")  # printify_pending.add's via: "<app> (<id>, <by>...)"
_SKIP = (OSError, ValueError, RuntimeError)  # each queue's own error is a RuntimeError


def _short(text) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= SUMMARY_MAX else s[:SUMMARY_MAX - 3] + "..."


def _audit_rows(item_id: str) -> list[dict]:
    """The client audit trail's rows about this request id, oldest first."""
    import clients
    try:
        lines = clients.AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        if item_id not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("item") == item_id:
            rows.append(row)
    return rows


def _from_audit(item_id: str, queue: str, prefix: str, summary_of, ttl_s: float | None = None) -> dict | None:
    """A decided memory write or Proxmox change, from its audit rows ("<prefix> waiting", then approved/declined)."""
    rows = _audit_rows(item_id)
    asked = next((r for r in rows if r.get("action") == f"{prefix} waiting"), None)
    if asked is None:
        return None
    out = {"id": item_id, "queue": queue, "client": asked.get("client") or "owner", "summary": summary_of(asked),
           "asked_at": asked.get("t")}
    for r in rows:
        for status in ("approved", "declined"):
            if r.get("action") == f"{prefix} {status}":
                return {**out, "status": status, "decided_at": r.get("t"), "decided_by": "owner"}
    if ttl_s and time.time() - float(asked.get("t") or 0) >= ttl_s:
        return {**out, "status": "expired", "decided_at": float(asked.get("t") or 0) + ttl_s}
    return None  # asked, not decided, not waiting: nothing to say for sure


def _memory(item_id: str) -> dict | None:
    import memory_pending as mp
    try:
        item = next((i for i in mp.list_pending() if i["id"] == item_id), None)
    except _SKIP:
        item = None
    if item:
        return {"id": item_id, "queue": "memory", "client": item.get("client"), "status": "waiting",
                "summary": _short(item.get("content")), "asked_at": item.get("at")}
    return _from_audit(item_id, "memory", "shared memory write",
                       lambda r: f"a write to the shared memory (session {r.get('session_id')})")


def _proxmox(item_id: str) -> dict | None:
    import proxmox_pending as pp
    try:
        item = next((i for i in pp.list_pending() if i["id"] == item_id), None)
    except _SKIP:
        item = None
    if item:
        kind = (item.get("action") or {}).get("op") if isinstance(item.get("action"), dict) else None
        return {"id": item_id, "queue": "proxmox", "client": item.get("client"), **({"kind": kind} if kind else {}),
                "status": "waiting", "summary": _short(item.get("summary")), "asked_at": item.get("at")}
    return _from_audit(item_id, "proxmox", "proxmox change", lambda r: _short(r.get("change")), ttl_s=pp.TTL_S)


def _commerce_decider(row: dict) -> str | None:
    if row.get("outcome") == "declined":
        return "owner"
    if row.get("outcome") != "approved":
        return None
    m = _VIA_RE.match(str(row.get("note") or ""))
    if not m:
        return "owner"  # the Control Panel's approvals bar
    by = m.group(1).strip()
    if by.lower().startswith("bot:"):
        return "bot:" + by[4:].strip()
    return "auto" if by.lower().startswith("auto") else "owner"  # else the owner's own approval inside the app


def _commerce(item_id: str) -> dict | None:
    import printify_pending as pp
    try:
        item = next((i for i in pp.list_pending() if i["id"] == item_id), None)
    except _SKIP:
        item = None
    if item:
        return {"id": item_id, "queue": "commerce", "client": item.get("client"), "kind": item.get("action") or "publish",
                "status": "waiting", "summary": _short(item.get("summary")), "asked_at": item.get("at")}
    try:
        row = next((r for r in pp.history(pp.HISTORY_S // 86400) if r.get("id") == item_id), None)
    except _SKIP:
        row = None
    if not row or row.get("outcome") not in ("approved", "declined", "expired", "withdrawn", "parked"):
        return None
    by = _commerce_decider(row)
    return {"id": item_id, "queue": "commerce", "client": row.get("client"), "kind": row.get("kind") or "publish",
            "status": row["outcome"], "summary": _short(row.get("summary")), "asked_at": row.get("asked_at"),
            "decided_at": row.get("decided_at"), **({"decided_by": by} if by else {})}


def _inbox(item_id: str) -> dict | None:
    import owner_inbox
    try:
        item = next((i for i in owner_inbox._load() if i["id"] == item_id), None)
    except _SKIP:
        return None
    if not item:
        return None
    out = {"id": item_id, "queue": "inbox", "client": item.get("client"), "kind": item.get("need"),
           "summary": _short(item.get("text")), "asked_at": item.get("at")}
    if item.get("done_at"):
        return {**out, "status": "approved", "decided_at": item["done_at"], "decided_by": "owner"}
    return {**out, "status": "waiting"}


def status(request_id: str, client: str | None = None) -> dict:
    """{"id", "queue", "kind"?, "status", "summary", "asked_at", "decided_at"?, "decided_by"?} for one request, or
    {"id", "status": "unknown"}. client: the asking app (None: the owner, who sees every request)."""
    item_id = str(request_id or "").strip().lower()
    if ID_RE.match(item_id):
        for look in (_memory, _proxmox, _commerce, _inbox):
            try:
                found = look(item_id)
            except _SKIP:  # a queue that can't be read is skipped, never the whole answer
                found = None
            if found is None:
                continue
            asker = found.pop("client", None) or "owner"
            if client is not None and asker != client:
                continue  # someone else's: as if it weren't there
            return found
    return {"id": str(request_id or "")[:80], "status": "unknown"}
