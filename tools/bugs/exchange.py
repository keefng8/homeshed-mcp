"""Known bugs shared with the HomeShed Library: the two stores act as one (capabilities/bugs/sync-contract.md).

HomeShed makes every call: POST {library}/bugs/exchange with a known-bugs pack v2 of what changed here, and the
library's reply carries what changed there since the cursor HomeShed last got. One address, one key, both directions.
It runs in the background after a bug changes here, and on a lookup when the last exchange is over an hour old (never
on a timer, and a failing tool call never waits for it). Anything the library hasn't acknowledged is simply sent
again next time, so there is no separate retry queue to lose.

Merge rules (the contract): match by origin + origin_id, else by fingerprint; status takes the higher of the order;
text goes to the higher edit counter (rev), a tie with different text is kept in conflicts for the owner; counts are
per store, the larger of each; a tombstone wins at an equal or higher rev; every text field is cleaned again on the
way in. No clock time is used to merge. A change that arrived by sync is never sent back (its hash is marked sent).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from paths import data_path
from tools.bugs import known

STATE_FILE = Path(os.environ.get("BUG_EXCHANGE_FILE") or data_path("observations/bug_exchange.json"))
ORDER = {s: n for n, s in enumerate(known.STATUSES)}  # open < workaround-only < fixed
TEXT = ("title", "symptom", "error_text", "root_cause", "workaround", "fix", "component", "guard")
STALE_S, TIMEOUT_S, MAX_BUGS = 3600, 10, 2000
HERE = "homeshed"
_running = threading.Lock()


def content_hash(b: dict) -> str:
    shared = {**{f: b.get(f, "") for f in TEXT}, "tags": sorted(b.get("tags") or []),
              "fix_status": b.get("fix_status", "open")}
    return hashlib.sha256(json.dumps(shared, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def record(b: dict) -> dict:
    """A stored bug as the contract's record."""
    occ = {**(b.get("remote_occurrences") or {}), HERE: len(b.get("occurrences") or [])}
    return {"origin": b.get("origin") or HERE, "origin_id": b.get("origin_id") or b["id"],
            **{f: b.get(f, "") for f in TEXT}, "tags": list(b.get("tags") or []),
            "fix_status": b.get("fix_status", "open"), "fp": b.get("fingerprint", ""), "fp_version": 1,
            "occurrences": occ, "regressed": dict(b.get("regressed") or {}), "rev": int(b.get("rev") or 1),
            "content_hash": content_hash(b), "deleted": bool(b.get("deleted")),
            "audience": b.get("audience") or "private"}


def _sent_key(r: dict) -> str:
    """What the library has of a record once it acknowledged it: a new count here is a change to send too."""
    return r["content_hash"] + str(r["rev"]) + json.dumps([r["occurrences"], r["regressed"], r["deleted"]],
                                                          sort_keys=True)


def _counts(value) -> dict:
    return {str(k)[:20]: int(v) for k, v in (value or {}).items()
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0} if isinstance(value, dict) else {}


def merge(bugs: dict, incoming: dict) -> str | None:
    """Merge one record from the library into the store (in place). The id it landed on, or None if refused."""
    if not isinstance(incoming, dict) or not str(incoming.get("origin_id") or "").strip():
        return None
    origin, origin_id = str(incoming.get("origin") or "library")[:20], str(incoming["origin_id"])[:80]
    fields = {f: known._clean(f, incoming.get(f)) for f in TEXT}
    tags = [known._clean("component", t) for t in (incoming.get("tags") or []) if str(t).strip()][:10]
    status = incoming.get("fix_status") if incoming.get("fix_status") in ORDER else "open"
    rev = incoming.get("rev") if isinstance(incoming.get("rev"), int) and incoming["rev"] > 0 else 1
    fp = known.fingerprint(fields["error_text"])  # ours, from the cleaned text: never trust a sent fingerprint
    mine = next((b for b in bugs.values() if (b.get("origin") or HERE) == origin
                 and (b.get("origin_id") or b["id"]) == origin_id), None)
    if mine is None and fp:
        mine = next((b for b in bugs.values() if b.get("fingerprint") == fp), None)
    if mine is None:
        if len(fields["title"]) < 3 or incoming.get("deleted"):
            return None
        bid = "LIB-" + "".join(c for c in origin_id if c.isalnum() or c in "._-")[:60]
        bugs[bid] = {"id": bid, **fields, "fix_status": status, "tags": tags, "fingerprint": fp, "occurrences": [],
                     "audience": "private", "origin": origin, "origin_id": origin_id, "rev": rev,
                     "remote_occurrences": {k: v for k, v in _counts(incoming.get("occurrences")).items() if k != HERE},
                     "regressed": {k: v for k, v in _counts(incoming.get("regressed")).items() if k != HERE},
                     "created": known._now(), "updated": known._now()}
        return bid
    if ORDER.get(status, 0) > ORDER.get(mine.get("fix_status"), 0):
        mine["fix_status"] = status
    my_rev = int(mine.get("rev") or 1)
    if incoming.get("deleted") and rev >= my_rev:
        mine["deleted"] = True
    if rev > my_rev:
        for f, v in fields.items():
            if v or f not in ("title",):
                mine[f] = v if f != "title" or len(v) >= 3 else mine[f]
        mine["tags"], mine["rev"] = tags, rev
        mine["fingerprint"] = known.fingerprint(mine.get("error_text", "")) or mine.get("fingerprint", "")
    elif rev == my_rev and any(fields[f] and fields[f] != mine.get(f, "") for f in TEXT):
        theirs = {f: fields[f] for f in TEXT if fields[f] and fields[f] != mine.get(f, "")}
        mine["conflicts"] = ([c for c in mine.get("conflicts") or [] if c != theirs] + [theirs])[-3:]
    for key in ("occurrences", "regressed"):
        store = "remote_occurrences" if key == "occurrences" else "regressed"
        have = dict(mine.get(store) or {})
        for k, v in _counts(incoming.get(key)).items():
            if k != HERE:
                have[k] = max(have.get(k, 0), v)
        mine[store] = have
    mine["updated"] = known._now()
    return mine["id"]


def _state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def _address() -> tuple[str, str]:
    import runtime_settings
    import vault
    url = str(runtime_settings.get("library_url") or "").strip().rstrip("/")
    return url, (vault.secret("LIBRARY_BUGS_KEY") or "") if url else ""


def exchange(post=None) -> dict:
    """One exchange with the library, now: send what it hasn't acknowledged, merge what it sends back.
    post(url, key, pack) -> the library's reply; the default is an HTTP POST. Never raises for a network failure."""
    url, key = _address()
    if not url or not key:
        return {"skipped": "no library linked (Settings > Known bugs: library address, and its key in Credentials)"}
    if not _running.acquire(blocking=False):
        return {"skipped": "an exchange is already running"}
    try:
        state = _state()
        sent = state.get("sent") if isinstance(state.get("sent"), dict) else {}
        bugs = known._load()
        outgoing = [r for r in (record(b) for b in bugs.values() if not b["id"].startswith("PRO-"))
                    if sent.get(r["origin"] + ":" + r["origin_id"]) != _sent_key(r)][:MAX_BUGS]
        pack = {"formatVersion": 2, "kind": "known-bugs", "from": HERE, "since": state.get("cursor"), "bugs": outgoing}
        try:  # the "Caught by HomeShed" counter rides along; an unreadable failure store just leaves it out
            from tools.bugs import seen
            pack["catches"] = seen.catches()
        except seen.SeenError:
            pass
        try:
            reply = (post or _post)(url, key, pack)
        except Exception as exc:  # noqa: BLE001 - the library down or unreachable: try again next time
            state.update(last_try=time.time(), last_error=str(exc)[:200])
            _save_state(state)
            return {"ok": False, "error": state["last_error"], "pending": len(outgoing)}
        reply = reply if isinstance(reply, dict) else {}
        skipped = reply.get("skipped") if isinstance(reply.get("skipped"), dict) else {}
        for r in outgoing:  # acknowledged: not sent again until it changes; one the library skipped goes again
            if r["origin_id"] not in skipped:
                sent[r["origin"] + ":" + r["origin_id"]] = _sent_key(r)
        merged = 0
        with known._lock:
            bugs = known._load()
            for incoming in (reply.get("bugs") or [])[:MAX_BUGS]:
                bid = merge(bugs, incoming)
                if bid:
                    merged += 1
                    r = record(bugs[bid])
                    sent[r["origin"] + ":" + r["origin_id"]] = _sent_key(r)  # no echo
            if merged:
                known._save(bugs)
        state.update(sent=sent, cursor=reply.get("cursor", state.get("cursor")), last_ok=time.time(),
                     last_try=time.time(), last_error="", catches=str(reply.get("catches") or "")[:200])
        _save_state(state)
        return {"ok": True, "sent": len(outgoing), "received": merged}
    finally:
        _running.release()


def _post(url: str, key: str, pack: dict) -> dict:
    import httpx
    r = httpx.post(f"{url}/bugs/exchange", json=pack, timeout=TIMEOUT_S, headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    return r.json()


def soon(only_if_stale: bool = False) -> None:
    """Exchange in the background (after a change, or on a lookup when the last one is over an hour old)."""
    try:
        url, key = _address()
    except Exception:  # noqa: BLE001 - settings or vault unreadable: nothing to share with
        return
    if not url or not key:
        return
    if only_if_stale and time.time() - float(_state().get("last_try") or 0) < STALE_S:
        return
    threading.Thread(target=exchange, name="bug-exchange", daemon=True).start()
