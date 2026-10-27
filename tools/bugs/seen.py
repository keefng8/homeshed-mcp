"""bugs.seen: failed tool calls become suggested bugs, so nobody has to remember to report one (agents
reported bugs only when asked to). See ../../capabilities/bugs/known.md.

The PostToolUseFailure hook (.claude/hooks/known-bug-hint.py) sends every failure here. Each one is sorted first:
- noise: our own guard blocks, denials, the user stopping a call, hook timeouts. Not a failure of anything.
- misuse: the tool was used wrongly (an Edit whose text isn't there, a Read of a missing file, bad arguments, a test
  run failing mid-development, a bare exit code). The platform worked.
- platform: anything else. Only these can become bugs.
Every class is counted, with one cleaned example, so the sorting can be checked against real events (R&D 2026-10-24:
"log one raw event per class before filtering").

A platform failure that matches a known bug adds one occurrence to it, once per session (retries don't inflate it).
Otherwise its fingerprint is watched; seen in 2 different sessions it is suggested, never in one session alone (one
session's retries would trigger that for free). Suggestions wait in their own file, not in the known bugs, so bugs.find
never returns unreviewed noise: the owner keeps one (it becomes a known bug) or dismisses it (that error is never
suggested again). Everything stays on this machine; every stored text is redacted first.

Platform failures are also counted per day ("caught", and how many met a known bug with a fix) for the library's
"Caught by HomeShed" box: catches() is the week's total, sent with each bug exchange (sync-contract.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from registry import tool
from paths import data_path
from tools.bugs import known

SEEN_FILE = Path(os.environ.get("BUG_CANDIDATES_FILE") or data_path("observations/bug_candidates.json"))
SUGGEST_AT_SESSIONS = 2
KEEP_SESSIONS, KEEP_DAYS, MAX_WATCHED, FORGET_AFTER_DAYS = 20, 30, 500, 14
EXAMPLE_CHARS, TAIL_CHARS = 600, 400
CAUGHT_DAYS = 8
_lock = threading.Lock()

# Matched on stable tags and tool messages, not on prose that changes (R&D: "match on the guard's tag").
NOISE = [
    ("guard", re.compile(r"\bGuard [a-z0-9_.-]+ \(|\bguard [a-z0-9]+\.[a-z0-9_.-]+\b|PreToolUse:\w+ hook", re.I)),
    ("denied", re.compile(r"permission[^.\n]{0,40}denied|doesn't want to (proceed|take this action)|user (rejected|denied|"
                          r"declined)|auto[- ]mode classifier|blocked by the classifier|not permitted in this mode",
                          re.I)),
    ("interrupted", re.compile(r"interrupted by (the )?user|request (was )?(aborted|cancelled|canceled)", re.I)),
    ("hook-timeout", re.compile(r"hook\b.{0,40}timed? ?out", re.I)),
]
MISUSE = [
    ("edit-mismatch", re.compile(r"string to replace not found|old_string|found \d+ matches of the string|"
                                 r"no changes to make", re.I)),
    ("not-read", re.compile(r"has not been read yet|read it first|must read the file", re.I)),
    ("bad-arguments", re.compile(r"InputValidationError|invalid (tool )?(parameters|arguments)|"
                                 r"missing required (argument|parameter)|unexpected keyword argument", re.I)),
    ("tests-failing", re.compile(r"\b\d+ failed\b|^FAILED \S+::|\bTests?:\s+\d+ failed|AssertionError", re.I | re.M)),
]
MISUSE_TOOLS = {"missing-file": ({"Read", "Glob", "Grep", "NotebookEdit", "Edit", "Write"},
                                 re.compile(r"does not exist|no such file|not found|cannot find", re.I))}
BARE_EXIT = re.compile(r"^\s*(exit code|exited with( code)?|error:?)\s*-?\d+\s*$", re.I)


class SeenError(ValueError):
    """Bad input or an unusable store; plain words, never a secret."""


def tail(error_text: str) -> str:
    """The error's last three non-empty lines: a traceback or a shell failure ends with the part that names it."""
    lines = [ln.strip() for ln in str(error_text or "").splitlines() if ln.strip()]
    return " ".join(lines[-3:])[-TAIL_CHARS:]


def classify(error_text: str, tool_name: str = "", interrupted: bool = False) -> str:
    """"noise:<why>", "misuse:<why>" or "platform"."""
    text = str(error_text or "")
    if interrupted:
        return "noise:interrupted"
    for name, rx in NOISE:
        if rx.search(text):
            return f"noise:{name}"
    if len(text.strip()) < 12 or BARE_EXIT.match(text):
        return "misuse:bare-exit"
    for name, rx in MISUSE:
        if rx.search(text):
            return f"misuse:{name}"
    base = str(tool_name or "").split(":")[0]
    for name, (tools, rx) in MISUSE_TOOLS.items():
        if base in tools and rx.search(text):
            return f"misuse:{name}"
    if base == "Bash" and str(tool_name).split(":")[-1] in ("grep", "rg", "rtk"):
        return "misuse:shell-search"
    return "platform"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> dict:
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"classes": {}, "watched": {}}
    except (OSError, ValueError):
        raise SeenError("the bug-suggestions file is unreadable; restore or delete it") from None
    if not isinstance(data, dict) or not isinstance(data.get("watched"), dict):
        raise SeenError("the bug-suggestions file is malformed; restore or delete it")
    data.setdefault("classes", {})
    return data


def _save(data: dict) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, SEEN_FILE)


def _session_key(session: str) -> str:
    return hashlib.sha1(str(session or "unknown").encode("utf-8")).hexdigest()[:10]


def _prune(watched: dict, now: datetime) -> None:
    """Forget what is only being watched and hasn't come back in two weeks; keep the file bounded."""
    for key in [k for k, w in watched.items() if w.get("status") == "watching"
                and (now - datetime.fromisoformat(w["last"])).days > FORGET_AFTER_DAYS]:
        del watched[key]
    if len(watched) > MAX_WATCHED:
        for key in sorted((k for k, w in watched.items() if w.get("status") == "watching"),
                          key=lambda k: watched[k]["last"])[:len(watched) - MAX_WATCHED]:
            del watched[key]


def _count_occurrence(bug_id: str, session_key: str) -> bool:
    """One more occurrence of a known bug, unless this session already counted one. True when counted."""
    with known._lock:
        bugs = known._load()
        bug = bugs.get(bug_id)
        if bug is None:
            return False
        occurrences = bug.setdefault("occurrences", [])
        if any(o.get("session") == session_key for o in occurrences):
            return False
        occurrences.append({"at": known._now(), "by": "hook", "session": session_key})
        if bug.get("fix_status") == "fixed":
            known.regressed(bug)
        bug["updated"] = known._now()
        known._save(bugs)
    known._shared()  # the library's count of this bug's occurrences follows
    return True


def _has_fix(bug_id: str) -> bool:
    bug = known._load().get(bug_id) or {}
    return bool(str(bug.get("fix") or "").strip() or str(bug.get("workaround") or "").strip())


def _caught(data: dict, now: datetime, with_fix: bool) -> None:
    days = data.get("caught") if isinstance(data.get("caught"), dict) else {}
    day = days.get(now.date().isoformat())
    day = day if isinstance(day, list) and len(day) == 2 else [0, 0]
    days[now.date().isoformat()] = [day[0] + 1, day[1] + int(with_fix)]
    data["caught"] = {d: days[d] for d in sorted(days)[-CAUGHT_DAYS:]}


def _send_catches() -> None:
    """The counts changed: the library gets them with the next exchange, at most about once an hour."""
    from tools.bugs import exchange
    exchange.soon(only_if_stale=True)


def catches(now: datetime | None = None) -> dict:
    """The library's "Caught by HomeShed" block: platform failures from the start of the UTC day 6 days ago, and
    how many met a known bug with a fix or workaround."""
    now = now or _now()
    start = datetime.combine((now - timedelta(days=6)).date(), datetime.min.time(), tzinfo=timezone.utc)
    week = with_fix = 0
    for day, counts in (_load().get("caught") or {}).items():
        if (str(day) >= start.date().isoformat() and isinstance(counts, list) and len(counts) == 2
                and all(isinstance(n, int) and n >= 0 for n in counts)):
            week, with_fix = week + counts[0], with_fix + min(counts[1], counts[0])
    return {"since": int(start.timestamp()), "week": week, "with_fix": with_fix}


@tool(name="seen", category="bugs", doc="bugs/known.md")
def seen(error_text: str, tool_name: str = "", session: str = "", known_id: str = "", interrupted: bool = False) -> dict:
    """A tool call failed: count it, and suggest it as a bug once it comes back in another session. Sent by the
    PostToolUseFailure hook; there is no need to call it by hand (bugs.report records a bug you have understood).

    Args:
        error_text: the error as printed.
        tool_name: the tool that failed; for Bash, "Bash:<first word of the command>".
        session: the session's id (stored only as a short hash).
        known_id: the known bug bugs.find matched it to, if any: that bug gets one more occurrence (once per session).
        interrupted: the user stopped the call.
    Returns:
        {"class": "noise:..." | "misuse:..." | "platform", and for platform: "counted" (known_id) or "status"
        ("watching" | "suggested" | "dismissed" | "kept") and "sessions"}.
    """
    import runtime_settings
    if runtime_settings.get("bug_capture") is False:
        return {"class": "off"}
    text = str(error_text or "")
    tool_name = known._clean("component", tool_name)
    cls = classify(text, tool_name, bool(interrupted))
    now, skey = _now(), _session_key(session)
    with _lock:
        data = _load()
        c = data["classes"].setdefault(cls, {"count": 0})
        c["count"] += 1
        c["last"] = now.isoformat(timespec="seconds")
        c.setdefault("example", known.sanitize(tail(text))[:EXAMPLE_CHARS])
        c.setdefault("tool", tool_name)
        if cls != "platform":
            _save(data)
            return {"class": cls}
        matched = bool(known_id and known.BUG_ID.fullmatch(str(known_id)))
        _caught(data, now, matched and _has_fix(known_id))
        if matched:
            _save(data)
            result = {"class": cls, "known_id": known_id, "counted": _count_occurrence(known_id, skey)}
            _send_catches()
            return result
        key = known.fingerprint(f"{tool_name.split(':')[0]} {tail(text)}")
        w = data["watched"].get(key)
        if w is None:
            w = data["watched"][key] = {"tool": tool_name, "example": known.sanitize(text.strip()[-EXAMPLE_CHARS:]),
                                        "first": now.isoformat(timespec="seconds"), "count": 0, "sessions": [],
                                        "days": [], "status": "watching"}
        w["count"] += 1
        w["last"] = now.isoformat(timespec="seconds")
        if skey not in w["sessions"]:
            w["sessions"] = (w["sessions"] + [skey])[-KEEP_SESSIONS:]
        day = now.date().isoformat()
        if day not in w["days"]:
            w["days"] = (w["days"] + [day])[-KEEP_DAYS:]
        if w["status"] == "watching" and len(w["sessions"]) >= SUGGEST_AT_SESSIONS:
            w["status"] = "suggested"
            w["items"] = now.isoformat(timespec="seconds")
        _prune(data["watched"], now)
        _save(data)
    _send_catches()
    return {"class": cls, "status": w["status"], "sessions": len(w["sessions"])}


def _view(key: str, w: dict) -> dict:
    return {"key": key, "tool": w.get("tool", ""), "example": w.get("example", ""), "count": w.get("count", 0),
            "sessions": len(w.get("sessions") or []), "days": len(w.get("days") or []), "first": w.get("first"),
            "last": w.get("last"), "status": w.get("status"), "kb": w.get("kb", "")}


def suggestions() -> dict:
    """For the Control Panel: suggested bugs, most widespread first, and how every failure was sorted."""
    data = _load()
    items = [_view(k, w) for k, w in data["watched"].items() if w.get("status") == "suggested"]
    items.sort(key=lambda v: (-v["days"], -v["sessions"], -v["count"], v["key"]))
    watching = sum(1 for w in data["watched"].values() if w.get("status") == "watching")
    return {"items": items, "watching": watching, "classes": data["classes"],
            "kept": sum(1 for w in data["watched"].values() if w.get("status") == "kept"),
            "dismissed": sum(1 for w in data["watched"].values() if w.get("status") == "dismissed")}


def decide(key: str, action: str, title: str = "", symptom: str = "") -> dict:
    """Owner only (a custom route): keep (it becomes a known bug, private, "open") or dismiss (never suggested again)."""
    if action not in ("keep", "dismiss"):
        raise SeenError("action must be keep or dismiss")
    with _lock:
        data = _load()
        w = data["watched"].get(str(key))
        if w is None or w.get("status") != "suggested":
            raise SeenError("that suggestion isn't waiting any more")
        if action == "keep":
            title = str(title or "").strip() or f"{w.get('tool') or 'A tool'} fails: {tail(w.get('example', ''))}"[:120]
            bug = known.report(title[:120], symptom=symptom, error_text=w.get("example", ""), component=w.get("tool", ""),
                               source="suggested", tags=["items"])
            w["kb"] = bug["id"]
        w["status"] = "kept" if action == "keep" else "dismissed"
        w["decided"] = _now().isoformat(timespec="seconds")
        _save(data)
        return _view(str(key), w)
