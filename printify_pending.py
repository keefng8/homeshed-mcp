"""Printify publishes, and updates of live products, waiting for the owner (to-do #59, 2026-10-07; updates 2026-10-08).

printify.product.publish never publishes from a tool call, whoever calls (an app or the owner's own sessions): it
checks the product and queues the publish here. printify.product.update_live queues an update of a published product
the same way (action "update", the change in "payload"); its approval sends the edit and a selective publish. The owner approves it in the Control Panel's approvals bar (admin route
POST /printify/pending/<id>/approve, owner token only) and only then is it sent to Printify, after checking again.
Decline drops it. Same shape as proxmox_pending.py; each step goes to the client audit trail.

Rules this module keeps:
- Bounded: MAX_TOTAL waiting.
- A publish older than TTL_S can't be approved (the draft may have changed since): it's dropped on the next look.
- A publish that fails when approved stays waiting with its error, so the owner can retry or decline.
- An unreadable file refuses new publishes and decisions (it isn't silently emptied).
- At most PRINTIFY_PUBLISH_DAILY_MAX publishes in 24 hours (default 5), and PRINTIFY_LIVE_UPDATE_DAILY_MAX live
  updates (default 10), counted when the owner approves.
- One waiting request per product, whatever its kind.
- Every outcome is logged for the asking app (HISTORY_FILE, 7 days: approved, declined, expired), so it can check
  with commerce.requests.list instead of assuming something still waits (the owner, 2026-10-08: "when i approve from
  the dash, does the agent get informed?"). An owner's private add-on (private_decision_hooks.py) may also pass each
  decision on as it happens; it runs in the background and never holds up or fails a decision.
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

PENDING_FILE = Path(os.environ.get("PRINTIFY_PENDING_FILE") or data_path("usage/printify_pending.json"))
DONE_FILE = Path(os.environ.get("PRINTIFY_PUBLISHED_FILE") or data_path("usage/printify_published.json"))
HISTORY_FILE = Path(os.environ.get("PRINTIFY_DECISIONS_FILE") or data_path("usage/printify_decisions.json"))
MAX_TOTAL = 30
TTL_S = 24 * 3600
HISTORY_S = 7 * 86400
ID_RE = re.compile(r"^[0-9a-f]{8}$")
_lock = threading.Lock()


class PendingError(RuntimeError):
    """A publish that can't be queued, or a decision on one that isn't waiting."""


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, min(50, int(os.environ.get(name) or default)))
    except ValueError:
        return default


def daily_max() -> int:
    return _env_int("PRINTIFY_PUBLISH_DAILY_MAX", 5)


def update_daily_max() -> int:
    return _env_int("PRINTIFY_LIVE_UPDATE_DAILY_MAX", 10)


def _load() -> list[dict]:
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise PendingError(f"the file of Printify publishes waiting for the owner ({PENDING_FILE}) is unreadable; "
                           "fix or remove it") from None
    items = data.get("items") if isinstance(data, dict) else None
    now = time.time()
    ok = [i for i in items or [] if isinstance(i, dict) and ID_RE.match(str(i.get("id", "")))]
    for i in ok:
        if now - float(i.get("at") or 0) >= TTL_S:  # logged once (by id), then gone at the next save
            _log(i, "expired", f"not decided within {TTL_S // 3600} hours", at=float(i.get("at") or 0) + TTL_S)
    return [i for i in ok if now - float(i.get("at") or 0) < TTL_S]


def history(days: int = 7) -> list[dict]:
    """The owner's decisions of the last `days` (at most 7), newest first."""
    try:
        rows = json.loads(HISTORY_FILE.read_text(encoding="utf-8")).get("decisions") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    since = time.time() - min(days, 7) * 86400
    return sorted((r for r in rows if isinstance(r, dict) and float(r.get("decided_at") or 0) >= since),
                  key=lambda r: -float(r.get("decided_at") or 0))


def _log(item: dict, outcome: str, note: str = "", at: float | None = None) -> None:
    """One outcome into HISTORY_FILE (once per request id), and to the owner's decision hook if there is one."""
    row = {"id": item.get("id"), "kind": item.get("action") or "publish", "client": item.get("client"),
           "product_id": item.get("product_id"), "summary": item.get("summary"), "asked_at": item.get("at"),
           "decided_at": at or time.time(), "outcome": outcome, **({"note": str(note)[:300]} if note else {})}
    rows = history(7)
    if any(r.get("id") == row["id"] for r in rows):
        return
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"decisions": ([row] + rows)[:1000]}, indent=1), encoding="utf-8")
        os.replace(tmp, HISTORY_FILE)
    except OSError:
        pass  # the decision itself stands; only its record is missing
    _announce(row)


def _announce(row: dict) -> None:
    """Pass a decision to the owner's private add-on (private_decision_hooks.decided), in the background: a slow or
    broken hook never holds up or fails a decision. Without one, nothing is sent anywhere."""
    try:
        import private_decision_hooks
    except ImportError:
        return

    def run():
        try:
            private_decision_hooks.decided(dict(row))
        except Exception:  # noqa: BLE001 - the hook is a courtesy; the history file has the outcome either way
            pass
    threading.Thread(target=run, daemon=True).start()


def _save(items: list[dict]) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": items}, indent=1), encoding="utf-8")
    os.replace(tmp, PENDING_FILE)


def _done_rows() -> list[dict]:
    """Every approved request of the last 24 hours, of any kind."""
    try:
        rows = json.loads(DONE_FILE.read_text(encoding="utf-8")).get("published") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    return [r for r in rows if isinstance(r, dict) and time.time() - float(r.get("at") or 0) < 86400]


def done_today(action: str) -> list[dict]:
    return [r for r in _done_rows() if (r.get("action") or "publish") == action]


def published_today() -> list[dict]:
    return done_today("publish")


def _record(row: dict) -> None:
    rows = _done_rows() + [row]
    DONE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DONE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"published": rows[-200:]}, indent=1), encoding="utf-8")
    os.replace(tmp, DONE_FILE)


def add(product_id: str, summary: str, checks: dict, action: str = "publish", payload: dict | None = None) -> dict:
    """Queue one checked request (a publish, or a live product's update with its change in payload). Returns what the
    caller is told: {pending, id, publish, expires_in_h, how}."""
    import clients
    client = clients.current_client.get()
    with _lock:
        items = _load()
        if any(i.get("product_id") == product_id for i in items):
            raise PendingError("that product is already waiting for the owner's approval (a publish or an update)")
        if len(items) >= MAX_TOTAL:
            raise PendingError(f"{MAX_TOTAL} requests are already waiting for the owner; try again once some are decided")
        item_id = secrets.token_hex(4)
        items.append({"id": item_id, "client": client or "owner", "product_id": product_id, "summary": summary,
                      "checks": checks, "at": time.time(), "action": action, **({"payload": payload} if payload else {})})
        _save(items)
    clients._audit(f"printify {action} waiting", client, item=item_id, product=product_id)
    return {"pending": True, "id": item_id, action: summary, "expires_in_h": TTL_S // 3600,
            "how": "Waiting for the owner: nothing has changed on the shop yet. The owner approves it in the Control "
                   "Panel's approvals bar (or declines it)."}


def list_pending() -> list[dict]:
    with _lock:
        return sorted(_load(), key=lambda i: -float(i.get("at") or 0))


def decide(item_id: str, approve: bool) -> dict:
    """Approve (check again, then run it now, as the owner) or decline (drop it) one waiting request."""
    if not ID_RE.match(str(item_id)):
        raise PendingError("that isn't a waiting request's id")
    import clients
    from tools.commerce import _http as h
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            raise PendingError(f"no request {item_id} is waiting (already decided, or older than {TTL_S // 3600} hours?)")
        action = item.get("action") or "publish"
        result: dict = {"id": item_id, "approved": bool(approve), action: item.get("summary")}
        if approve:
            if action == "publish":
                from tools.printify.publish import execute, station_pause
                if paused := station_pause():  # approving while the machine is stopped waits too (it stays queued)
                    raise PendingError(paused + "; it stays waiting")
                if len(published_today()) >= daily_max():
                    raise PendingError(f"{daily_max()} products were published in the last 24 hours: that's the daily "
                                       "limit (PRINTIFY_PUBLISH_DAILY_MAX); it stays waiting")
                run = lambda: execute(item["product_id"])  # noqa: E731
            elif action == "update":
                from tools.printify.update_live import execute_update
                if len(done_today("update")) >= update_daily_max():
                    raise PendingError(f"{update_daily_max()} live products were updated in the last 24 hours: that's "
                                       "the daily limit (PRINTIFY_LIVE_UPDATE_DAILY_MAX); it stays waiting")
                run = lambda: execute_update(item["product_id"], item.get("payload") or {})  # noqa: E731
            elif action == "etsy":  # an Etsy listing's details (tools/etsy/write.py, 2026-10-08)
                from tools.etsy.write import edit_daily_max, execute as execute_etsy
                if len(done_today("etsy")) >= edit_daily_max():
                    raise PendingError(f"{edit_daily_max()} Etsy listings were edited in the last 24 hours: that's "
                                       "the daily limit (ETSY_EDIT_DAILY_MAX); it stays waiting")
                run = lambda: execute_etsy(item.get("payload") or {})  # noqa: E731
            else:
                raise PendingError(f"request {item_id} is of an unknown kind ({action}): decline it")
            try:
                result["result"] = run()
            except h.CommerceError as exc:
                item["last_error"] = str(exc)[:300]
                _save(items)
                clients._audit(f"printify {action} failed", item.get("client"), item=item_id, error=str(exc)[:200])
                _announce({"id": item_id, "kind": action, "client": item.get("client"), "summary": item.get("summary"),
                           "product_id": item.get("product_id"), "decided_at": time.time(), "outcome": "failed",
                           "note": "approved, but it failed and stays waiting: " + str(exc)[:240]})
                raise
            _record({"at": time.time(), "product_id": item["product_id"], "by": item.get("client"), "action": action,
                     **({"old": (item.get("payload") or {}).get("old")} if action == "update" else {})})  # for undo
        _log(item, "approved" if approve else "declined",
             str((result.get("result") or {}).get("note") or "") if approve else "declined by the owner")
        _save([i for i in items if i["id"] != item_id])
    clients._audit(f"printify {action} " + ("approved" if approve else "declined"), item.get("client"), item=item_id,
                   product=item.get("product_id"))
    lid = (result.get("result") or {}).get("reapply_etsy") if approve else None
    if lid:  # the director's rule: a re-publish never leaves the listing's Etsy details undone (queued after the lock)
        from tools.commerce import _http as h
        from tools.etsy.write import queue_reapply
        try:
            result["etsy_reapply"] = queue_reapply(str(lid))
        except (h.CommerceError, PendingError) as exc:
            result["etsy_reapply_error"] = f"re-apply the Etsy details by hand (etsy.listing.update reapply=true): {exc}"
    return result
