"""Messages from agents to the owner, and his replies (2026-10-06).

The owner: "a notification like the others for agents to leave me a message. i can reply to them through it too, say
if i need to run a command, or you need something". An agent leaves one with inbox.send (say what it needs: just
information, a command for him to run, or a decision); it shows on the Control Panel's Messages pill, he replies or
marks it done there (admin routes in server.py, owner token only), and the agent reads the answer with inbox.replies.

Rules this module keeps:
- Bounded: MAX_OPEN open messages and length limits, so a busy agent can't fill the disk or the panel.
- A message marked done drops out after KEEP_DONE_S; an open one stays until he answers it.
- An unreadable file refuses new messages and replies (it isn't silently emptied).
- A command is only shown to the owner, never run from here.
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

INBOX_FILE = Path(os.environ.get("OWNER_INBOX_FILE") or data_path("usage/owner_inbox.json"))
NEEDS = ("info", "run_command", "decision", "other")
MAX_OPEN = 100
MAX_TEXT, MAX_COMMAND, MAX_SENDER, MAX_REPLIES = 2000, 1000, 80, 20
KEEP_DONE_S = 7 * 86400
ID_RE = re.compile(r"^[0-9a-f]{8}$")
_lock = threading.Lock()


class InboxError(RuntimeError):
    """A message that can't be left, or a reply to one that isn't there."""


def _load() -> list[dict]:
    try:
        data = json.loads(INBOX_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise InboxError(f"the owner's message file ({INBOX_FILE}) is unreadable; fix or remove it") from None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    now = time.time()
    return [i for i in items if isinstance(i, dict) and ID_RE.match(str(i.get("id", "")))
            and not (i.get("done_at") and now - float(i["done_at"]) > KEEP_DONE_S)]


def _save(items: list[dict]) -> None:
    INBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = INBOX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": items}, indent=1), encoding="utf-8")
    os.replace(tmp, INBOX_FILE)


def _text(value, most: int, what: str, required: bool = True) -> str:
    s = str(value or "").strip()
    if required and not s:
        raise InboxError(f"{what} is empty")
    if len(s) > most:
        raise InboxError(f"{what} is longer than {most} characters")
    return s


def send(sender: str, text: str, need: str = "info", command: str = "", client: str = "owner") -> dict:
    sender = _text(sender, MAX_SENDER, "sender", required=False) or client
    text = _text(text, MAX_TEXT, "the message")
    if need not in NEEDS:
        raise InboxError(f"need must be one of {', '.join(NEEDS)}")
    command = _text(command, MAX_COMMAND, "the command", required=need == "run_command")
    with _lock:
        items = _load()
        if sum(not i.get("done_at") for i in items) >= MAX_OPEN:
            raise InboxError(f"{MAX_OPEN} messages are already waiting for the owner; try again once some are answered")
        item = {"id": secrets.token_hex(4), "sender": sender, "client": client, "need": need, "text": text,
                "command": command, "at": time.time(), "replies": [], "done_at": None}
        items.append(item)
        _save(items)
    return item


def _find(items: list[dict], item_id: str) -> dict:
    for i in items:
        if i["id"] == item_id:
            return i
    raise InboxError(f"no message {item_id!r}")


def reply(item_id: str, text: str, done: bool = False) -> dict:
    text = _text(text, MAX_TEXT, "the reply")
    with _lock:
        items = _load()
        item = _find(items, item_id)
        item["replies"] = (item.get("replies") or [])[-(MAX_REPLIES - 1):] + [{"text": text, "at": time.time()}]
        if done:
            item["done_at"] = time.time()
        _save(items)
    return item


def mark_done(item_id: str) -> dict:
    with _lock:
        items = _load()
        item = _find(items, item_id)
        item["done_at"] = item.get("done_at") or time.time()
        _save(items)
    return item


MAX_EDITS = 5


def _own(items: list[dict], item_id: str, sender: str, client: str) -> dict:
    """The sender's own message: same token (client) and, when given, same sender name. Nobody else's."""
    item = _find(items, item_id)
    if item.get("client") != client or (sender and item.get("sender") != sender):
        raise InboxError(f"no message {item_id!r} from you")
    return item


def amend(item_id: str, sender: str = "", client: str = "owner", text: str | None = None,
          command: str | None = None, need: str | None = None) -> dict:
    """Correct a message the owner hasn't closed (the owner, 2026-10-06: an agent told him to run a command, then
    corrected it in a second message). The old wording is kept under "edits" and the panel marks it edited."""
    with _lock:
        items = _load()
        item = _own(items, item_id, sender, client)
        if item.get("done_at"):
            raise InboxError("that message is already closed: send a new one instead")
        new_need = item["need"] if need is None else need
        if new_need not in NEEDS:
            raise InboxError(f"need must be one of {', '.join(NEEDS)}")
        new_text = item["text"] if text is None else _text(text, MAX_TEXT, "the message")
        new_cmd = item.get("command", "") if command is None else _text(command, MAX_COMMAND, "the command", required=False)
        if new_need == "run_command" and not new_cmd:
            raise InboxError("the command is empty: a run_command message needs one")
        old = {"text": item["text"], "command": item.get("command", ""), "need": item["need"], "at": time.time()}
        item["edits"] = (item.get("edits") or [])[-(MAX_EDITS - 1):] + [old]
        item.update(text=new_text, command=new_cmd, need=new_need, edited_at=time.time())
        _save(items)
    return item


def withdraw(item_id: str, sender: str = "", client: str = "owner") -> dict:
    """Take back a message (sent by mistake, or no longer needed). It leaves the owner's list at once."""
    with _lock:
        items = _load()
        item = _own(items, item_id, sender, client)
        items.remove(item)
        _save(items)
    return {"withdrawn": item_id}


def list_open() -> list[dict]:
    """Open messages, newest first: what the owner's Messages pill shows."""
    return sorted((i for i in _load() if not i.get("done_at")), key=lambda i: -float(i.get("at") or 0))


def for_sender(sender: str, client: str = "owner") -> list[dict]:
    """A sender's own messages with the owner's replies, newest first. An app's token sees only its own."""
    return sorted((i for i in _load() if i.get("client") == client and (not sender or i.get("sender") == sender)),
                  key=lambda i: -float(i.get("at") or 0))
