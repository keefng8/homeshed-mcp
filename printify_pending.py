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
- At most PRINTIFY_PUBLISH_DAILY_MAX publishes in 24 hours (default 100), and PRINTIFY_LIVE_UPDATE_DAILY_MAX live
  updates (default 10), counted when the owner approves.
- One waiting request per product, whatever its kind.
- Every outcome is logged for the asking app (HISTORY_FILE, 7 days: approved, declined, expired, withdrawn), so it can check
  with commerce.requests.list instead of assuming something still waits (the owner, 2026-10-08: "when i approve from
  the dash, does the agent get informed?"). An owner's private add-on (private_decision_hooks.py) may also pass each
  decision on as it happens; it runs in the background and never holds up or fails a decision.
- One approval, not two (the owner, 2026-10-09: "I don't need to double approve ... One is enough"). An app the owner
  names in the setting approvals_trusted_clients may pass owner_approval {"id", "by", "at"}: the owner's own approval
  inside that app. The request is queued and then approved at once through decide(), so the daily limits and the
  re-checks still apply. It's audited, and each approval id works only once. The tool server can't see the app's
  approval, so this trusts the app's word: it's off until the owner names the app. Without owner_approval, or when a
  check fails, the request waits here as before; a run that fails waits too.
- AUTO with zero taps (the owner, 2026-10-09: "auto and full auto on means zero taps from me ... important info to
  my phone only"): an approval whose "by" is an automatic actor ("auto...") counts only for
  an app named in approvals_trusted_auto_clients. The phone hears only what matters: a publish that ran, and anything
  that couldn't run and now waits for the owner.
- A bot's approval (the owner, 2026-10-14: a trusted bot may approve Etsy listing details that match the plan): "by"
  as "bot:<name>" counts only for an Etsy details edit and only for a bot named in approvals_trusted_bots (empty by
  default). It's logged as the bot's, so nobody mistakes it for the owner's own tap. Since 2026-10-25 it also counts for a live
  product's words (title, description, tags), never its prices or which variants sell: those still wait for the owner.
- A live update whose product changed since it was asked (update_live.Stale) is withdrawn, not left waiting for the
  owner, and the asker asks again. The STALE_PARK_AFTER-th on one product in STALE_WINDOW_S is logged "parked": a
  trusted bot or the owner checks those, and no new update of that product is queued for PARKED_S.
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
APPROVALS_FILE = Path(os.environ.get("PRINTIFY_APP_APPROVALS_FILE") or data_path("usage/app_approvals.json"))
TRUST_SETTING = "approvals_trusted_clients"
AUTO_TRUST_SETTING = "approvals_trusted_auto_clients"
BOT_TRUST_SETTING = "approvals_trusted_bots"  # bots whose "bot:<name>" approval counts, for BOT_ACTIONS only
BOT_ACTIONS = ("etsy", "update")  # Etsy listing details (owner, 2026-10-14) and a live product's words (2026-10-25),
# never a publish
STALE_FILE = Path(os.environ.get("PRINTIFY_STALE_FILE") or data_path("usage/printify_stale.json"))
STALE_WINDOW_S = 48 * 3600
STALE_PARK_AFTER = 3  # the first withdrawal and two failed re-asks (2026-10-25)
PARKED_S = 24 * 3600
APPROVAL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{3,80}$")
APPROVAL_MAX_AGE_S = 3600
APPROVAL_SKEW_S = 300
APPROVALS_KEPT_S = 30 * 86400
_lock = threading.Lock()


class PendingError(RuntimeError):
    """A publish that can't be queued, or a decision on one that isn't waiting."""


class NotYet(PendingError):
    """An approval that can't run yet only because of a daily limit or the station's pause: it stays waiting, and an
    app's owner_approval on it runs by itself once there's room (retry_waiting)."""


def _env_int(name: str, default: int, top: int = 50) -> int:
    try:
        return max(0, min(top, int(os.environ.get(name) or default)))
    except ValueError:
        return default


def daily_max() -> int:
    return _env_int("PRINTIFY_PUBLISH_DAILY_MAX", 100, top=100)  # a ceiling (each publish still needs the owner's tap): 5 until
    # 2026-10-09, 20 until 2026-10-11 (the owner: "raise to 100")


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


def _stale_rows() -> dict:
    """{product_id: {"n", "at", "parked_at"?}}: stale live updates per product, the last within STALE_WINDOW_S."""
    try:
        rows = json.loads(STALE_FILE.read_text(encoding="utf-8")).get("products") or {}
    except (OSError, ValueError, AttributeError):
        rows = {}
    return {k: v for k, v in rows.items() if isinstance(v, dict) and time.time() - float(v.get("at") or 0) < STALE_WINDOW_S}


def _save_stale(rows: dict) -> None:
    STALE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STALE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"products": rows}, indent=1), encoding="utf-8")
    os.replace(tmp, STALE_FILE)


def _count_stale(product_id) -> int:
    rows = _stale_rows()
    n = int((rows.get(str(product_id)) or {}).get("n") or 0) + 1
    rows[str(product_id)] = {"n": n, "at": time.time(), **({"parked_at": time.time()} if n >= STALE_PARK_AFTER else {})}
    _save_stale(rows)
    return n


def _clear_stale(product_id) -> None:
    rows = _stale_rows()
    if rows.pop(str(product_id), None) is not None:
        _save_stale(rows)


def parked(product_id) -> dict | None:
    """The product's stale row while it's parked (PARKED_S after the STALE_PARK_AFTER-th stale update), else None."""
    row = _stale_rows().get(str(product_id))
    return row if row and time.time() - float(row.get("parked_at") or 0) < PARKED_S else None


def trusted_clients(setting: str = TRUST_SETTING) -> set[str]:
    """The apps whose own owner approval counts (approvals_trusted_clients), or whose AUTO mode counts as the owner's
    approval (approvals_trusted_auto_clients); none unless the owner names one."""
    try:
        import runtime_settings
        raw = str(runtime_settings.get(setting) or "")
    except Exception:  # noqa: BLE001 - no settings readable: no app is trusted
        return set()
    return {c.strip() for c in raw.split(",") if c.strip()}


def _used_approvals() -> dict:
    try:
        rows = json.loads(APPROVALS_FILE.read_text(encoding="utf-8")).get("used") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {k: v for k, v in rows.items() if time.time() - float(v.get("at") or 0) < APPROVALS_KEPT_S}


def _approval_problem(client: str | None, ok, action: str = "publish", payload: dict | None = None) -> str | None:
    """Why this owner_approval can't be used, or None. A bot's ("bot:<name>") counts only for BOT_ACTIONS (a live
    update only when it changes words, not prices or variants) and only for a bot the owner named
    (approvals_trusted_bots): it's labelled as the bot's, never passed off as the owner's."""
    from datetime import datetime
    auto_ok = bool(client) and client in trusted_clients(AUTO_TRUST_SETTING)
    if not client or not (auto_ok or client in trusted_clients()):
        return (f"approvals made inside {client or 'this caller'} aren't trusted here: the owner can name the app in "
                "Settings > Shops and social > \"Apps whose own approval counts\"")
    if not isinstance(ok, dict) or not APPROVAL_ID_RE.match(str(ok.get("id") or "")):
        return 'owner_approval needs "id" (3-80 letters, digits, _ . : -), "by" and "at"'
    by = str(ok.get("by") or "").strip()
    if not 0 < len(by) <= 60:
        return '"by" must say who approved it (1-60 characters)'
    if by.lower().startswith("auto") and not auto_ok:
        return (f'"by" is an automatic actor, and {client}\'s AUTO mode doesn\'t count as the owner\'s approval: the '
                'owner can name the app in Settings > Shops and social > "Apps whose AUTO mode counts as your approval"')
    if by.lower().startswith("bot:"):
        bot = by[4:].strip().lower()
        if action not in BOT_ACTIONS:
            return (f'a bot\'s approval ("{by}") counts only for Etsy listing details and a live product\'s words, '
                    f'not for a {action}')
        if action == "update" and ((payload or {}).get("prices") or (payload or {}).get("enabled")):
            return f'a bot\'s approval ("{by}") doesn\'t count for a live price or variant change: that waits for the owner'
        if bot not in {b.lower() for b in trusted_clients(BOT_TRUST_SETTING)}:
            return (f'"{by}" isn\'t a bot the owner lets approve: the owner can name it in Settings > Shops and social '
                    '> "Bots that may approve listing details and live product words"')
    try:
        at = datetime.fromisoformat(str(ok.get("at")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return '"at" must be an ISO time, e.g. 2026-10-09T12:30:00Z'
    if not -APPROVAL_SKEW_S <= time.time() - at <= APPROVAL_MAX_AGE_S:
        return f"the approval is from {datetime.fromtimestamp(at).isoformat(timespec='minutes')}: it must be from the last hour"
    if str(ok["id"]) in _used_approvals():
        return f"approval {ok['id']} was already used"
    return None


def _use_approval(ok: dict, client: str, item_id: str) -> None:
    rows = _used_approvals()
    rows[str(ok["id"])] = {"at": time.time(), "client": client, "by": str(ok["by"])[:60], "item": item_id}
    APPROVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = APPROVALS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"used": rows}, indent=1), encoding="utf-8")
    os.replace(tmp, APPROVALS_FILE)


def _note(message: str, title: str, priority: str, tag: str) -> None:
    try:
        from tools.notify.send import send
        send(message, title=title, priority=priority, tags=[tag])
    except Exception:  # noqa: BLE001 - the note is a courtesy; the audit log and history have it either way
        pass


def add(product_id: str, summary: str, checks: dict, action: str = "publish", payload: dict | None = None,
        owner_approval: dict | None = None) -> dict:
    """Queue one checked request (a publish, or a live product's update with its change in payload). Returns what the
    caller is told: {pending, id, publish, expires_in_h, how}. With a usable owner_approval (see the module notes) it
    is approved at once: {pending: false, id, ..., result}."""
    import clients
    from tools.commerce import _http as h
    client = clients.current_client.get()
    problem = (_approval_problem(client, owner_approval, action, payload) if owner_approval is not None
               else "none given")
    with _lock:
        if action == "update" and parked(product_id):
            raise PendingError(f"updates of {product_id} went stale {STALE_PARK_AFTER} times running: it's parked: a trusted bot "
                               "or the owner checks these (commerce.requests.list, outcome \"parked\"), not queued again yet")
        items = _load()
        if any(i.get("product_id") == product_id for i in items):
            raise PendingError("that product is already waiting for the owner's approval (a publish or an update)")
        if len(items) >= MAX_TOTAL:
            raise PendingError(f"{MAX_TOTAL} requests are already waiting for the owner; try again once some are decided")
        item_id = secrets.token_hex(4)
        item = {"id": item_id, "client": client or "owner", "product_id": product_id, "summary": summary,
                "checks": checks, "at": time.time(), "action": action, **({"payload": payload} if payload else {})}
        if problem is None:
            item["owner_approval"] = {k: str(owner_approval[k])[:80] for k in ("id", "by", "at")}
            _use_approval(owner_approval, client, item_id)  # before running: an id never works twice
        items.append(item)
        _save(items)
    clients._audit(f"printify {action} waiting", client, item=item_id, product=product_id)
    waiting = {"pending": True, "id": item_id, action: summary, "expires_in_h": TTL_S // 3600,
               "how": "Waiting for the owner: nothing has changed on the shop yet. The owner approves it in the Control "
                      "Panel's approvals bar (or declines it)."}
    if owner_approval is None:
        return waiting
    if problem:
        clients._audit(f"printify {action} app approval not used", client, item=item_id, reason=problem[:200])
        return {**waiting, "owner_approval": f"not used: {problem}"}
    by = str(owner_approval["by"])
    try:
        done = decide(item_id, True, via=f"{client} ({owner_approval['id']}, {by})")
    except NotYet as exc:  # only a daily limit or the pause: it runs by itself later (retry_waiting); no phone note,
        # as the owner has nothing to do (the owner, 2026-10-09: "important info to my phone only")
        return {**waiting, "owner_approval": f"used; not run yet: {exc}. It runs by itself when there's room."}
    except h.CommerceError as exc:
        if getattr(exc, "stale", False):  # withdrawn in decide(): nothing waits, so no phone note
            return {"pending": False, "id": item_id, action: summary, "withdrawn": True, "error": str(exc)[:300]}
        _note(f"{summary}. It was approved in {client} ({by}) but couldn't run: {exc}. Approve or decline it in the "
              "Control Panel.", f"Waiting for you: {client}", "high", "warning")
        return {**waiting, "owner_approval": f"used, but it didn't run: {exc}. It waits in the Control Panel."}
    except PendingError as exc:
        _note(f"{summary}. It was approved in {client} ({by}) but couldn't run: {exc}. Approve or decline it in the "
              "Control Panel.", f"Waiting for you: {client}", "high", "warning")
        return {**waiting, "owner_approval": f"used, but it didn't run: {exc}. It waits in the Control Panel."}
    _published_note(action, summary, client, by, owner_approval["id"])
    return {"pending": False, "id": item_id, action: summary, "approved_in": client,
            "owner_approval": owner_approval["id"], "result": done.get("result"),
            **{k: done[k] for k in ("etsy_reapply", "etsy_reapply_error") if k in done}}


def _published_note(action: str, summary: str, client, by: str, approval_id: str) -> None:
    if action == "publish":  # only what matters reaches the phone (the owner, 2026-10-09): a new listing going live
        _note(f"{summary}. Approved in {client} by {by} ({approval_id}).",
              f"Published by {client}" + (" (AUTO)" if by.lower().startswith("auto") else ""), "default",
              "white_check_mark")


def retry_waiting() -> list[dict]:
    """Run the waiting requests an app's owner_approval already approved but a daily limit or the station's pause held
    back (the owner, 2026-10-09: "yes to auto retries"), oldest first, through decide() so every check runs again.
    One that fails for any other reason keeps its error and waits for the owner. Returns what ran or failed."""
    from tools.commerce import _http as h
    out = []
    for item in sorted(list_pending(), key=lambda i: float(i.get("at") or 0)):
        ok = item.get("owner_approval")
        if not ok or item.get("last_error"):
            continue
        by = str(ok.get("by") or "")
        try:
            done = decide(item["id"], True, via=f"{item.get('client')} ({ok.get('id')}, {by}; run when there was room)")
        except NotYet:
            continue  # still no room: the next round tries again
        except (PendingError, h.CommerceError) as exc:
            if not any(i["id"] == item["id"] for i in list_pending()):
                continue  # the owner decided it meanwhile (or it expired): nothing to tell
            if isinstance(exc, PendingError):
                continue  # held for another reason that isn't the owner's to fix now; the next round looks again
            _note(f"{item.get('summary')}. Approved in {item.get('client')} ({by}) but couldn't run: {exc}. Approve or "
                  "decline it in the Control Panel.", f"Waiting for you: {item.get('client')}", "high", "warning")
            out.append({"id": item["id"], "error": str(exc)[:200]})
            continue
        _published_note(item.get("action") or "publish", item.get("summary"), item.get("client"), by, str(ok.get("id")))
        out.append({"id": item["id"], "ran": True, **{k: done[k] for k in ("etsy_reapply",) if k in done}})
    return out


RETRY_S = 600


def start() -> None:
    """Every RETRY_S, run what retry_waiting finds (from serve_http, beside proxmox_monitor.start)."""
    def loop():
        while True:
            time.sleep(RETRY_S)
            try:
                retry_waiting()
            except Exception:  # noqa: BLE001 - a bad round never stops the next one
                pass
    threading.Thread(target=loop, name="printify-retry", daemon=True).start()


def list_pending() -> list[dict]:
    with _lock:
        return sorted(_load(), key=lambda i: -float(i.get("at") or 0))


def decide(item_id: str, approve: bool, via: str = "") -> dict:
    """Approve (check again, then run it now, as the owner) or decline (drop it) one waiting request. via: where the
    owner approved it when not in the Control Panel (an app's own approval, see add)."""
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
                    raise NotYet(paused + "; it stays waiting")
                if len(published_today()) >= daily_max():
                    raise NotYet(f"{daily_max()} products were published in the last 24 hours: that's the daily "
                                 "limit (PRINTIFY_PUBLISH_DAILY_MAX); it stays waiting")
                run = lambda: execute(item["product_id"])  # noqa: E731
            elif action == "update":
                from tools.printify.update_live import execute_update
                if len(done_today("update")) >= update_daily_max():
                    raise NotYet(f"{update_daily_max()} live products were updated in the last 24 hours: that's "
                                 "the daily limit (PRINTIFY_LIVE_UPDATE_DAILY_MAX); it stays waiting")
                run = lambda: execute_update(item["product_id"], item.get("payload") or {})  # noqa: E731
            elif action == "etsy":  # an Etsy listing's details (tools/etsy/write.py, 2026-10-08)
                from tools.etsy.write import edit_daily_max, execute as execute_etsy
                if len(done_today("etsy")) >= edit_daily_max():
                    raise NotYet(f"{edit_daily_max()} Etsy listings were edited in the last 24 hours: that's "
                                 "the daily limit (ETSY_EDIT_DAILY_MAX); it stays waiting")
                run = lambda: execute_etsy(item.get("payload") or {})  # noqa: E731
            elif action == "etsy_shop":  # the shop's own texts and sections (tools/etsy/shop_write.py, 2026-10-10)
                from tools.etsy.shop_write import daily_max as shop_max, execute as execute_shop
                if len(done_today("etsy_shop")) >= shop_max():
                    raise NotYet(f"{shop_max()} Etsy shop edits were made in the last 24 hours: that's the daily "
                                 "limit (ETSY_SHOP_EDIT_DAILY_MAX); it stays waiting")
                run = lambda: execute_shop(item.get("payload") or {})  # noqa: E731
            elif action == "pinterest_pin":  # one pin, the owner's choice each time (tools/pinterest/pins.py)
                from tools.pinterest.pins import daily_max as pin_max, execute_pin
                if len(done_today("pinterest_pin")) >= pin_max():
                    raise NotYet(f"{pin_max()} pins were made in the last 24 hours: that's the daily limit "
                                 "(PINTEREST_DAILY_MAX); it stays waiting")
                run = lambda: execute_pin(item.get("payload") or {})  # noqa: E731
            elif action == "pinterest_board":
                from tools.pinterest.pins import execute_board
                run = lambda: execute_board(item.get("payload") or {})  # noqa: E731
            else:
                raise PendingError(f"request {item_id} is of an unknown kind ({action}): decline it")
            try:
                with h.counted_as(f"approvals:{action}"):  # the Etsy request counter, when no tool call is running
                    result["result"] = run()
            except h.CommerceError as exc:
                if "HTTP 429" in str(exc) and item.get("owner_approval"):
                    # a shop's per-second limit (2026-10-20: an Etsy details edit hit 429 and then sat
                    # waiting): nothing changed on the shop, so an app-approved request is held like a daily limit
                    # and retry_waiting runs it again next round, instead of waiting for the owner
                    clients._audit(f"printify {action} rate limited", item.get("client"), item=item_id)
                    raise NotYet(f"the shop said too many requests (HTTP 429); it runs again by itself in a few "
                                 "minutes") from None
                if getattr(exc, "stale", False):  # the product changed since: withdrawn, the asker asks again
                    n = _count_stale(item.get("product_id"))
                    park = n >= STALE_PARK_AFTER
                    _log(item, "parked" if park else "withdrawn",
                         f"{exc}" + (f" ({n} times running: parked)" if park else ""))
                    _save([i for i in items if i["id"] != item_id])
                    clients._audit(f"printify {action} " + ("parked" if park else "withdrawn"), item.get("client"),
                                   item=item_id, product=item.get("product_id"), stale=n)
                    raise
                item["last_error"] = str(exc)[:300]
                _save(items)
                clients._audit(f"printify {action} failed", item.get("client"), item=item_id, error=str(exc)[:200])
                _announce({"id": item_id, "kind": action, "client": item.get("client"), "summary": item.get("summary"),
                           "product_id": item.get("product_id"), "decided_at": time.time(), "outcome": "failed",
                           "note": "approved, but it failed and stays waiting: " + str(exc)[:240]})
                raise
            _record({"at": time.time(), "product_id": item["product_id"], "by": item.get("client"), "action": action,
                     **({"old": (item.get("payload") or {}).get("old")} if action == "update" else {})})  # for undo
            if action == "update":
                _clear_stale(item.get("product_id"))
        note = str((result.get("result") or {}).get("note") or "") if approve else "declined by the owner"
        _log(item, "approved" if approve else "declined", f"approved in {via}. {note}" if via else note)
        _save([i for i in items if i["id"] != item_id])
    clients._audit(f"printify {action} " + ("approved" if approve else "declined"), item.get("client"), item=item_id,
                   product=item.get("product_id"), **({"via": via} if via else {}))
    lid = (result.get("result") or {}).get("reapply_etsy") if approve else None
    if lid:  # the re-publish rule: a re-publish never leaves the listing's Etsy details undone (queued after the lock)
        from tools.commerce import _http as h
        from tools.etsy.write import queue_reapply
        try:
            with h.counted_as("approvals:etsy_reapply"):
                result["etsy_reapply"] = queue_reapply(str(lid))
        except (h.CommerceError, PendingError) as exc:
            result["etsy_reapply_error"] = f"re-apply the Etsy details by hand (etsy.listing.update reapply=true): {exc}"
    return result


def withdraw(item_id: str, reason: str) -> dict:
    """Take a waiting request back without running it, when it turned out not to be needed (2026-10-09: five Etsy
    re-applies queued after words-only updates, every detail checked intact on Etsy). Not the owner's decision: it's
    logged "withdrawn" with the reason, not "declined"."""
    if not ID_RE.match(str(item_id)):
        raise PendingError("that isn't a waiting request's id")
    if not (reason or "").strip():
        raise PendingError("say why it's withdrawn")
    import clients
    with _lock:
        items = _load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            raise PendingError(f"no request {item_id} is waiting (already decided, or older than {TTL_S // 3600} hours?)")
        _log(item, "withdrawn", reason.strip())
        _save([i for i in items if i["id"] != item_id])
    action = item.get("action") or "publish"
    clients._audit(f"printify {action} withdrawn", item.get("client"), item=item_id, reason=reason.strip()[:200])
    return {"id": item_id, "withdrawn": True, action: item.get("summary")}
