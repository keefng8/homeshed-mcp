"""Who's who among the agents: each one's name, role and how to reach it, confirmed by the owner (2026-10-24).

Claude Code's ListAgents shows names only, and sessions forgot which was which ("Environment setup" is the library
builder). Any agent may add itself or another (a cloud session, a mailbox-only app) with agents.roster_set; the owner
confirms each row in the Control Panel's approvals bar. Rules:
- The owner's wording wins: once a row is confirmed, an agent's change waits beside it as `proposed` until the owner
  confirms it. Every Claude session shares the owner's token, so only the Control Panel's route counts as the owner.
- A row nobody has set or seen for STALE_DAYS shows as gone; it's never deleted except by the owner.
- Bounded: MAX_ROWS rows, short fields, control characters removed.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from paths import data_path

STORE_FILE = Path(os.environ.get("AGENTS_ROSTER_FILE") or data_path("usage/agents_roster.json"))
REACH = {"session": "SendMessage (a session on the owner's PC)",
         "cloud": "a cloud session: SendMessage there; it answers through its own tools once the owner allows it",
         "mailbox": "agents.send (the tool server's mailbox)"}
MAX_ROWS, MAX_NAME, MAX_ROLE, STALE_DAYS = 50, 60, 200, 7
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
_lock = threading.Lock()


class RosterError(RuntimeError):
    """A change that can't be made. The message is safe to show the caller."""


def key_of(name: str) -> str:
    return " ".join(name.split()).lower()


def _clean(text: str, limit: int, what: str) -> str:
    text = " ".join(CONTROL_RE.sub(" ", str(text or "")).split())
    if not text:
        raise RosterError(f"{what} is empty")
    if len(text) > limit:
        raise RosterError(f"{what} is over {limit} characters")
    return text


def _load() -> dict:
    try:
        data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise RosterError(f"the roster file ({STORE_FILE.name}) is unreadable; the owner must fix or remove it") from None
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(rows: dict) -> None:
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STORE_FILE)


def roster(now: float | None = None) -> dict:
    """Every row, confirmed first, then by name: {name, role, reach, how, confirmed, by, updated, gone, proposed?}."""
    now = time.time() if now is None else now
    out = []
    for row in _load().values():
        out.append({"name": row.get("name", ""), "role": row.get("role", ""), "reach": row.get("reach", ""),
                    "how": REACH.get(row.get("reach"), ""), "confirmed": bool(row.get("confirmed")),
                    "by": row.get("by", ""), "updated": row.get("updated", 0),
                    "gone": now - float(row.get("updated") or 0) > STALE_DAYS * 86400,
                    **({"proposed": row["proposed"]} if row.get("proposed") else {})})
    out.sort(key=lambda r: (not r["confirmed"], r["name"].lower()))
    return {"agents": out, "count": len(out)}


def set_row(name: str, role: str, reach: str, by: str, now: float | None = None) -> dict:
    """An agent adds or updates a row (itself or another). A confirmed row keeps the owner's wording: the change
    waits as `proposed`. Setting the same words again only marks the row seen."""
    name, role = _clean(name, MAX_NAME, "name"), _clean(role, MAX_ROLE, "role")
    if reach not in REACH:
        raise RosterError(f"reach must be one of {', '.join(REACH)}")
    now = time.time() if now is None else now
    with _lock:
        rows = _load()
        row = rows.get(key_of(name))
        if row is None:
            if len(rows) >= MAX_ROWS:
                raise RosterError(f"the roster is full ({MAX_ROWS} rows); the owner can remove gone ones")
            rows[key_of(name)] = row = {"name": name, "role": role, "reach": reach, "by": by, "confirmed": False}
            status = "added: waiting for the owner to confirm"
        elif (row.get("role"), row.get("reach")) == (role, reach):
            status = "unchanged"
        elif row.get("confirmed"):
            row["proposed"] = {"role": role, "reach": reach, "by": by}
            status = "proposed: the owner confirmed the current wording, so the change waits for them"
        else:
            row.update(role=role, reach=reach, by=by)
            status = "updated: waiting for the owner to confirm"
        row["updated"] = now
        _save(rows)
    return {"name": row["name"], "status": status}


def decide(name: str, action: str, role: str = "", now: float | None = None) -> dict:
    """The owner (Control Panel only): confirm (taking a proposed change, or `role` as their own wording), decline (drop
    a proposed change; a row never confirmed is removed) or remove."""
    if action not in ("confirm", "decline", "remove"):
        raise RosterError("action must be confirm, decline or remove")
    with _lock:
        rows = _load()
        row = rows.get(key_of(name))
        if row is None:
            raise RosterError("no such row")
        if action == "decline" and row.get("proposed"):
            del row["proposed"]
        elif action in ("decline", "remove"):
            del rows[key_of(name)]
        else:
            row.update(row.pop("proposed", None) or {})
            if role:
                row["role"] = _clean(role, MAX_ROLE, "role")
            row["confirmed"], row["by"] = True, "owner"
            row["updated"] = time.time() if now is None else now
        _save(rows)
    return {"name": name, "action": action}


def waiting() -> list[dict]:
    """Rows the owner hasn't confirmed, or with a change proposed: the approvals bar's list."""
    return [r for r in roster()["agents"] if not r["confirmed"] or r.get("proposed")]


def brief(limit: int = 12) -> str:
    """A few lines for the start of a session: who's who, unconfirmed rows marked."""
    lines = []
    for r in roster()["agents"]:
        if r["gone"]:
            continue
        lines.append(f"- {r['name']}: {r['role']} ({r['reach']}{'' if r['confirmed'] else ', unconfirmed'})")
    return "\n".join(lines[:limit])
