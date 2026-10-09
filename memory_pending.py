"""Writes an app asked to make to the shared memory, waiting for the owner.

The owner's decision (2026-10-01): each app has its own memory; reading the shared memory is on by
default, with a switch per app (clients.set_memory_shared_read); writing to it needs the owner's approval. So
memory.capture and memory.remember_fact with scope "global" from an app land here, not in the shared memory. The
owner approves on the web panel's API access page (the write then goes in as the owner's, and a fact's category
is indexed so recall_relevant finds it) or declines (it's dropped). Each step is in the client audit trail.

Rules this module keeps:
- Nothing waiting is ever read back as memory: the queue isn't a memory store.
- Bounded: MAX_PER_APP waiting per app and MAX_TOTAL in all, so a busy app can't fill the disk.
- An approval whose write fails keeps the item waiting, so nothing is lost.
- An unreadable file refuses new writes and decisions (it isn't silently emptied).
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

PENDING_FILE = Path(os.environ.get("MEMORY_PENDING_FILE") or data_path("usage/memory_pending.json"))
MAX_TOTAL = 200
MAX_PER_APP = 50
ID_RE = re.compile(r"^[0-9a-f]{8}$")
_lock = threading.Lock()


class PendingError(RuntimeError):
    """A write that can't be queued, or a decision on one that isn't waiting."""


def _load() -> list[dict]:
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise PendingError(f"the file of writes waiting for the owner ({PENDING_FILE}) is unreadable; "
                           "fix or remove it") from None
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict) and ID_RE.match(str(i.get("id", "")))] if isinstance(items, list) else []


def _save(items: list[dict]) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": items}, indent=1), encoding="utf-8")
    os.replace(tmp, PENDING_FILE)


def add(client: str, session_id: str, role: str, content: str) -> str:
    """Queue one shared-memory write from an app. Returns its id."""
    with _lock:
        items = _load()
        if len(items) >= MAX_TOTAL:
            raise PendingError(f"{MAX_TOTAL} writes to the shared memory are already waiting for the owner; "
                               "try again once some are decided")
        if sum(1 for i in items if i.get("client") == client) >= MAX_PER_APP:
            raise PendingError(f"this app already has {MAX_PER_APP} writes to the shared memory waiting for the owner")
        item_id = secrets.token_hex(4)
        items.append({"id": item_id, "client": client, "session_id": session_id, "role": role, "content": content,
                      "at": time.time()})
        _save(items)
    import clients
    clients._audit("shared memory write waiting", client, item=item_id, session_id=session_id)
    return item_id


def list_pending() -> list[dict]:
    """Every waiting write, newest first."""
    with _lock:
        return sorted(_load(), key=lambda i: -float(i.get("at") or 0))


def decide(item_id: str, approve: bool) -> dict:
    """Approve (write it to the shared memory as the owner's) or decline (drop it) one waiting write."""
    if not ID_RE.match(str(item_id)):
        raise PendingError("that isn't a waiting write's id")
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            raise PendingError(f"no write {item_id} is waiting (already decided?)")
        result: dict = {"id": item_id, "approved": bool(approve)}
        if approve:
            result["stored"] = _write(item)  # a MemoryError leaves it waiting
        _save([i for i in items if i["id"] != item_id])
    import clients
    clients._audit("shared memory write " + ("approved" if approve else "declined"), item.get("client"),
                   item=item_id, session_id=item.get("session_id"))
    return result


def _write(item: dict) -> dict:
    """The approved write, made as the owner's: the owner is the one who approved it."""
    import clients
    from tools.memory.capture import DEFAULT_FACT_CATEGORY, FACTS_SESSION_ID, capture
    token = clients.current_client.set(None)
    try:
        stored = capture(item["session_id"], item["content"], role=item.get("role") or "user", scope="global")
        sid = item["session_id"]
        if sid == FACTS_SESSION_ID or sid.startswith(FACTS_SESSION_ID + ":"):
            from tools.memory.recall_relevant import FACT_INDEX
            from tools.memory.remember_fact import _index
            category = sid.partition(":")[2] or DEFAULT_FACT_CATEGORY
            if category != FACT_INDEX:
                _index(category, "global")  # so memory.recall_relevant searches it too
    finally:
        clients.current_client.reset(token)
    return stored
