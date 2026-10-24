"""web.watch and web.watch.check: a phone alert when a web page changes. See ../../capabilities/web/watch.md.

R&D's verdict (researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md): build on web.read and difflib,
our own code: an ignore pattern for noisy lines, a trigger text, a minimum
size of change, and two checks in a row that agree before an alert, so one flaky load never buzzes the phone.
web.watch makes a schedule (schedule.*) that runs web.watch.check; the check keeps the page's last text.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import clients
from paths import data_path
from registry import tool
from tools.web.read import read as web_read

FILE_DEFAULT = "usage/web_watch.json"
MAX_LINES = 3000
MAX_WATCHES = 100
SHOW_LINES = 8
_lock = threading.Lock()


class WatchError(ValueError):
    """Safe to show."""


def _file() -> Path:
    return Path(os.environ.get("WEB_WATCH_FILE") or data_path(FILE_DEFAULT))


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, f)


def _pattern(ignore: str) -> re.Pattern | None:
    if not ignore:
        return None
    if len(ignore) > 200:
        raise WatchError("ignore is over 200 characters")
    try:
        return re.compile(ignore, re.IGNORECASE)
    except re.error as exc:
        raise WatchError(f"ignore isn't a valid pattern: {exc}") from None


def _lines(text: str, ignore: re.Pattern | None) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line and not (ignore and ignore.search(line)):
            out.append(line)
    return out[:MAX_LINES]


@tool(name="watch.check", category="web", doc="web/watch.md")
def check(url: str, ignore: str = "", trigger: str = "", min_change_chars: int = 20) -> dict:
    """Read a page and compare it with the last look. An alert is due only when the same change is seen on two checks
    in a row (one odd load never counts), and it's big enough, or the trigger text has just appeared. web.watch runs
    this on a schedule; call it directly to test a watch.

    Args:
        url: the public page.
        ignore: optional pattern; lines matching it are left out (e.g. "updated|views|\\d+ min ago").
        trigger: optional text; alert only when it newly appears on the page (e.g. "In stock"), not on other changes.
        min_change_chars: changes smaller than this (characters added plus removed) are ignored. Default 20.

    Returns:
        {"url", "status": "first_look"|"same"|"changed_once"|"changed", "alert": bool, "summary", "added": [...],
         "removed": [...]} (at most 8 lines each).
    """
    if isinstance(min_change_chars, bool) or not isinstance(min_change_chars, int) or min_change_chars < 0:
        raise WatchError("min_change_chars must be a whole number, 0 or more")
    pat = _pattern(ignore)
    trigger = str(trigger or "").strip()[:200]
    page = web_read(url)
    lines = _lines(page.get("content") or "", pat)
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    key = hashlib.sha256(json.dumps([page["url"], ignore, trigger]).encode()).hexdigest()[:16]
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with _lock:
        d = _load()
        st = d.get(key)
        if st is None:
            if len(d) >= MAX_WATCHES:
                raise WatchError(f"{MAX_WATCHES} pages are watched already")
            d[key] = {"url": page["url"], "lines": lines, "hash": digest, "at": now}
            _save(d)
            return {"url": page["url"], "status": "first_look", "alert": False, "added": [], "removed": [],
                    "summary": f"First look at {page['url']}: {len(lines)} lines kept to compare against."}
        if digest == st["hash"]:
            st.pop("pending", None)
            st["at"] = now
            _save(d)
            return {"url": page["url"], "status": "same", "alert": False, "added": [], "removed": [],
                    "summary": "No change."}
        diff = list(difflib.ndiff(st["lines"], lines))
        added = [x[2:] for x in diff if x.startswith("+ ")]
        removed = [x[2:] for x in diff if x.startswith("- ")]
        size = sum(len(x) for x in added) + sum(len(x) for x in removed)
        old_has = bool(trigger) and any(trigger.lower() in x.lower() for x in st["lines"])
        new_has = bool(trigger) and any(trigger.lower() in x.lower() for x in lines)
        due = (new_has and not old_has) if trigger else size >= min_change_chars
        if st.get("pending") != digest:  # first sight of this version: wait for the next check to agree
            st["pending"], st["at"] = digest, now
            _save(d)
            return {"url": page["url"], "status": "changed_once", "alert": False, "added": added[:SHOW_LINES],
                    "removed": removed[:SHOW_LINES], "summary": "Changed; waiting for the next check to agree."}
        st.update(lines=lines, hash=digest, at=now, changed_at=now)
        st.pop("pending", None)
        _save(d)
    if trigger:
        summary = f'"{trigger}" is now on {page["url"]}' if due else "Changed, but the trigger text didn't appear."
    else:
        summary = (f"{page['url']} changed: {len(added)} lines added, {len(removed)} removed. "
                   + " | ".join(f"+ {x[:120]}" for x in added[:3]))
    return {"url": page["url"], "status": "changed", "alert": due, "added": added[:SHOW_LINES],
            "removed": removed[:SHOW_LINES], "summary": summary[:700]}


@tool(name="watch", category="web", doc="web/watch.md")
def watch(url: str, every: str = "every 2 hours", name: str = "", ignore: str = "", trigger: str = "",
          min_change_chars: int = 20) -> dict:
    """Watch a web page and get a phone alert when it changes: a price, back in stock, a new post. Checks on a
    timetable (every 15 minutes at most often) and alerts only when two checks in a row agree on the change.

    Args:
        url: the public page.
        every: how often, as schedule.add takes it: "every 2 hours" (default), "every 30 minutes", "daily 08:00".
        name: shown in alerts; default "Watch: <the page>".
        ignore: optional pattern for noisy lines to leave out (e.g. "updated|\\d+ views").
        trigger: optional text; alert only when it appears (e.g. "In stock").
        min_change_chars: smaller changes are ignored. Default 20.

    Returns:
        the schedule (schedule.list and schedule.remove manage it), with its first look done.
    """
    from tools.schedule import engine
    args = {"url": url, "ignore": ignore, "trigger": trigger, "min_change_chars": min_change_chars}
    first = check(**args)  # proves the page reads and the options work before anything is scheduled
    s = engine.add(name or f"Watch: {first['url'][:60]}", "web.watch.check", args, every, "when_flagged",
                   clients.current_client.get())
    return {**s, "first_look": first["summary"]}
