"""Proxmox changes waiting for the owner (2026-10-03).

proxmox.vm.power / .vm.snapshot / .vm.create never change anything from a tool call, whoever calls (an app or the
owner's own sessions): with dry_run=false they validate the change, run the read-only checks, and queue it here. The
owner approves on the admin route POST /proxmox/pending/<id>/approve (owner token only; RequestGuard refuses client
tokens off /mcp) and only then does tools/proxmox/_client.execute() run it, after checking again. Decline drops it.
Same shape as memory_pending.py, and each step goes to the client audit trail.

Rules this module keeps:
- Bounded: MAX_TOTAL waiting, so a busy agent can't fill the disk.
- A change older than TTL_S can't be approved (the guest may have changed since): it's dropped on the next look.
- A change that fails when approved stays waiting with its error, so the owner can retry or decline.
- An unreadable file refuses new changes and decisions (it isn't silently emptied).
- Nothing here deletes a guest, a disk or a snapshot: there's no such op.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from paths import data_path

PENDING_FILE = Path(os.environ.get("PROXMOX_PENDING_FILE") or data_path("usage/proxmox_pending.json"))
MAX_TOTAL = 50
TTL_S = 24 * 3600
ID_RE = re.compile(r"^[0-9a-f]{8}$")
_lock = threading.Lock()


class PendingError(RuntimeError):
    """A change that can't be queued, or a decision on one that isn't waiting."""


def _load() -> list[dict]:
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise PendingError(f"the file of Proxmox changes waiting for the owner ({PENDING_FILE}) is unreadable; "
                           "fix or remove it") from None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    now = time.time()
    return [i for i in items if isinstance(i, dict) and ID_RE.match(str(i.get("id", "")))
            and now - float(i.get("at") or 0) < TTL_S]


def _save(items: list[dict]) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": items}, indent=1), encoding="utf-8")
    os.replace(tmp, PENDING_FILE)


def add(action: dict, summary: str, checks: dict) -> dict:
    """Queue one validated change. Returns what the caller is told: {pending, id, change, expires_in_h, how}."""
    import clients
    client = clients.current_client.get()
    with _lock:
        items = _load()
        if len(items) >= MAX_TOTAL:
            raise PendingError(f"{MAX_TOTAL} Proxmox changes are already waiting for the owner; "
                               "try again once some are decided")
        item_id = secrets.token_hex(4)
        items.append({"id": item_id, "client": client or "owner", "action": action, "summary": summary,
                      "checks": checks, "at": time.time()})
        _save(items)
    clients._audit("proxmox change waiting", client, item=item_id, change=summary)
    return {"pending": True, "id": item_id, "change": summary, "expires_in_h": TTL_S // 3600,
            "how": "Waiting for the owner: nothing has changed on Proxmox yet. The owner approves it on the tool "
                   f"server's admin route POST /proxmox/pending/{item_id}/approve (or declines it)."}


def list_pending() -> list[dict]:
    """Every waiting change, newest first."""
    with _lock:
        return sorted(_load(), key=lambda i: -float(i.get("at") or 0))


def decide(item_id: str, approve: bool) -> dict:
    """Approve (run it now, as the owner) or decline (drop it) one waiting change."""
    if not ID_RE.match(str(item_id)):
        raise PendingError("that isn't a waiting change's id")
    import clients
    from tools.proxmox._client import ProxmoxError, execute
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            raise PendingError(f"no Proxmox change {item_id} is waiting (already decided, or older than "
                               f"{TTL_S // 3600} hours?)")
        result: dict = {"id": item_id, "approved": bool(approve), "change": item.get("summary")}
        if approve:
            try:
                result["result"] = execute(item["action"])
            except ProxmoxError as exc:
                item["last_error"] = str(exc)[:300]
                _save(items)
                clients._audit("proxmox change failed", item.get("client"), item=item_id, error=str(exc)[:200])
                raise
        _save([i for i in items if i["id"] != item_id])
    clients._audit("proxmox change " + ("approved" if approve else "declined"), item.get("client"), item=item_id,
                   change=item.get("summary"))
    return result
