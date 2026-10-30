"""Task-state facts (R&D, 30 Oct 2026: a resumed session followed a decision that had since been replaced).

A fact saved with remember_fact(task=..., state=...) starts with a tag, "TASK[<name>] <state>: ". The newest fact
for a task name hides the older ones on recall, with no SUPERSEDES marker needed, and each one is labelled verified
(checked: tests ran, the live result was seen) or planned (intended, not yet checked), so a reader can tell them apart.
"""
from __future__ import annotations

import re

STATES = ("verified", "planned")
TASK_NAME_RE = re.compile(r"^[A-Za-z0-9][\w .:/-]{0,59}$")
TAG_RE = re.compile(r"^TASK\[([^\]\n]{1,60})\](?: (verified|planned))?: ")


def tagged(content: str, task: str, state: str) -> str:
    """content with its task tag in front. Raises ValueError for a bad name or state."""
    task, state = (task or "").strip(), (state or "").strip().lower()
    if not task:
        if state:
            raise ValueError("state needs a task name")
        return content
    if not TASK_NAME_RE.match(task):
        raise ValueError("task must be 1-60 letters, digits, spaces or _ . : / -")
    if state and state not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    return f"TASK[{task}]{' ' + state if state else ''}: {content}"


def label(message: dict) -> dict:
    """The message with "task" and "state" fields when its content carries a tag."""
    m = TAG_RE.match(str(message.get("content") or ""))
    if not m:
        return message
    return {**message, "task": m.group(1), "state": m.group(2) or ""}


def current_only(messages: list[dict]) -> list[dict]:
    """Label every tagged message, and keep only the newest one per task name (by timestamp, else list order with
    the first taken as newest, which is memory.recall's newest-first order). Untagged messages are untouched."""
    out = [label(m) for m in messages]
    newest: dict[str, tuple[str, int]] = {}
    for i, m in enumerate(out):
        if "task" in m:
            key, when = m["task"].lower(), str(m.get("timestamp") or "")
            if key not in newest or when > newest[key][0]:
                newest[key] = (when, i)
    keep = {i for _, i in newest.values()}
    return [m for i, m in enumerate(out) if "task" not in m or i in keep]
