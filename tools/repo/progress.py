"""repo.progress / repo.progress_update. See ../../capabilities/repo/progress.md.

Where an agent preparing a project for release reports how far it has got, so the owner sees it on a dashboard
(the agent gives the updates; the dashboard shows the percentage). Two numbers per project: release progress (pass/fail gates done out of all, each with its evidence) and
whatever metrics the agent measures, such as repo.readiness's "27 of 29". Items waiting on the owner keep the date
they were first reported, so the panel can say how long each has waited. The last 20 updates are kept.

The agent's name is the calling client's when it uses a client token (it can't claim another's); the owner's own
sessions may name themselves. Every update is checked: size caps, and no secrets or local paths in any text.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import clients
from paths import data_path
from registry import tool

STORE = Path(data_path("usage/repo_progress.json"))  # data_path gives a str (the live deploy caught it, 2026-09-30)
STATUSES = ("done", "doing", "waiting", "blocked", "todo")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# The card's project details (2026-10-01: where the project is made, its GitHub repo and link, and what's useful when
# prepping it). The folder itself is never stored: workspace is the project's registered name, and only the owner's
# panel turns it into a folder.
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
BRANCH = re.compile(r"^(?!.*\.\.)[A-Za-z0-9._/-]{1,100}$")
LICENCE = re.compile(r"^[A-Za-z0-9.+-]{1,40}$")
LINK = re.compile(r"^https://[^\s<>\"'`]{1,300}$")
MAX_STEPS, MAX_METRICS, MAX_WAITING, KEEP_HISTORY = 40, 20, 10, 20
LOCAL_PATH = re.compile(r"(?i)\b[a-z]:[\\/]|(?:^|[\s'\"(])/(?:home|Users|root|opt|mnt|srv)/")
_lock = threading.Lock()


class ProgressError(RuntimeError):
    """A bad project name or update: too big, a wrong status, or a secret or local path in the text."""


def _load() -> dict:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STORE)


def _clean(text, field: str, limit: int) -> str:
    """Short, single-line, and nothing that shouldn't be shown on a dashboard."""
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        raise ProgressError(f"{field} is longer than {limit} characters")
    from tools.secrets.transcript_scan import KINDS
    if any(rx.search(text) for _kind, rx, _action, _public in KINDS):
        raise ProgressError(f"{field} looks like it holds a secret: leave it out")
    if LOCAL_PATH.search(text):
        raise ProgressError(f"{field} names a local path: describe it instead (e.g. 'the assembled copy')")
    return text


def _matching(value: str, pattern: re.Pattern, field: str, what: str) -> str:
    value = str(value or "").strip()
    if value and not pattern.match(value):
        raise ProgressError(f"{field} must be {what}")
    return value


def _waiting_item(item) -> dict:
    """A line only the owner can act on: plain text, or {text, why?, how?, link?} (link: an https address)."""
    if isinstance(item, str):
        return {"text": _clean(item, "a waiting item", 120)}
    if not isinstance(item, dict) or not item.get("text"):
        raise ProgressError("each waiting item is a line of text, or {text, why?, how?, link?}")
    out = {"text": _clean(item["text"], "a waiting item", 120)}
    for key, limit in (("why", 200), ("how", 200)):
        if item.get(key):
            out[key] = _clean(item[key], f"a waiting item's {key}", limit)
    if item.get("link"):
        out["link"] = _matching(_clean(item["link"], "a waiting item's link", 300), LINK, "a waiting item's link",
                                "an https:// address")
    return out


def _percent(steps: list[dict]) -> int:
    return round(100 * sum(1 for s in steps if s["status"] == "done") / len(steps)) if steps else 0


def _summary(slug: str, p: dict) -> dict:
    steps = p.get("steps") or []
    details = dict(p.get("details") or {})
    if details.get("repo"):
        details["repo_url"] = f"https://github.com/{details['repo']}"
    return {"project": slug, "title": p.get("title") or slug, "percent": _percent(steps),
            "done": sum(1 for s in steps if s["status"] == "done"), "total": len(steps),
            "metrics": p.get("metrics") or {}, "waiting_on_owner": p.get("waiting") or [],
            "agent": p.get("agent"), "updated": p.get("updated"), "note": p.get("note", ""), **details}


@tool(name="progress_update", category="repo", doc="repo/progress.md")
def progress_update(project: str, title: str = "", steps: list | None = None, metrics: dict | None = None,
                    waiting_on_owner: list | None = None, note: str = "", agent: str = "", repo: str = "",
                    visibility: str = "", branch: str = "", version: str = "", licence: str = "", workspace: str = "",
                    next_step: str = "") -> dict:
    """Report how far a project's release has got, for the owner's dashboard.

    Args:
        project: a short name, lower case with dashes (e.g. "homeshed").
        title: what the project is, shown on the panel (e.g. "HomeShed public release").
        steps: the gates, [{id, title, status, evidence?}]; status is done, doing, waiting, blocked or todo. Replaces
            the previous list when given. At most 40.
        metrics: measured numbers, {name: short text}, e.g. {"readiness": "27 of 29", "tests": "917 passed"}.
        waiting_on_owner: what only the owner can do: short lines, or {text, why?, how?, link?} with an https link.
            Each keeps the date it was first reported.
        note: what this update checked, e.g. "checked 9afaeaf".
        agent: a display name, used only for the owner's own sessions (a client token's name always wins).
        repo: the GitHub repository, owner/name; the panel links to it.
        visibility: e.g. "private", "public" or "not public yet".
        branch: the branch releases come from, e.g. "main".
        version: the version being released, e.g. "v0.1.0".
        licence: its SPDX id, e.g. "Apache-2.0".
        workspace: the project's name in the tool server's project list (never a path): the owner's panel shows that
            project's folder from it.
        next_step: one line, what happens next.
        Any of repo..next_step left empty keeps what was stored.

    Returns:
        {project, percent, done, total, updated}.

    Raises:
        ProgressError: a bad project name; too many steps, metrics or waiting items; an unknown status; text over its
            limit; a malformed repo, branch, licence, workspace or link; or a secret or local path in any text.
    """
    if not SLUG.match(project or ""):
        raise ProgressError("project must be a short name: lower case letters, digits and dashes")
    caller = clients.current_client.get()
    who = caller or _clean(agent, "agent", 40) or "owner"
    clean_steps = None
    if steps is not None:
        if not isinstance(steps, list) or len(steps) > MAX_STEPS:
            raise ProgressError(f"steps must be a list of at most {MAX_STEPS}")
        clean_steps = []
        for s in steps:
            if not isinstance(s, dict) or s.get("status") not in STATUSES:
                raise ProgressError(f"each step needs a status: one of {', '.join(STATUSES)}")
            clean_steps.append({"id": _clean(s.get("id") or s.get("title"), "step id", 40),
                                "title": _clean(s.get("title") or s.get("id"), "step title", 80),
                                "status": s["status"], "evidence": _clean(s.get("evidence"), "step evidence", 120)})
    clean_metrics = None
    if metrics is not None:
        if not isinstance(metrics, dict) or len(metrics) > MAX_METRICS:
            raise ProgressError(f"metrics must be at most {MAX_METRICS} name: value pairs")
        clean_metrics = {_clean(k, "metric name", 30): _clean(v, "metric value", 60) for k, v in metrics.items()}
    if waiting_on_owner is not None and (not isinstance(waiting_on_owner, list) or len(waiting_on_owner) > MAX_WAITING):
        raise ProgressError(f"waiting_on_owner must be a list of at most {MAX_WAITING}")
    title, note = _clean(title, "title", 80), _clean(note, "note", 200)
    waiting = [_waiting_item(w) for w in waiting_on_owner] if waiting_on_owner is not None else None
    details = {k: v for k, v in (
        ("repo", _matching(repo, REPO, "repo", "owner/name, as on GitHub")),
        ("visibility", _clean(visibility, "visibility", 30)),
        ("branch", _matching(branch, BRANCH, "branch", "a branch name")),
        ("version", _clean(version, "version", 30)),
        ("licence", _matching(licence, LICENCE, "licence", "an SPDX id such as Apache-2.0")),
        ("workspace", _matching(workspace, SLUG, "workspace", "the project's short registered name, not a path")),
        ("next_step", _clean(next_step, "next step", 160))) if v}

    now = time.time()
    with _lock:
        data = _load()
        p = data.get(project) or {}
        if title:
            p["title"] = title
        if clean_steps is not None:
            p["steps"] = clean_steps
        if clean_metrics is not None:
            p["metrics"] = clean_metrics
        if waiting is not None:
            since = {w["text"]: w["since"] for w in p.get("waiting") or []}
            p["waiting"] = [{**w, "since": since.get(w["text"], now)} for w in waiting if w["text"]]
        if details:
            p["details"] = {**(p.get("details") or {}), **details}
        p.update(agent=who, updated=now, note=note)
        steps_now = p.get("steps") or []
        p["history"] = ([{"at": now, "agent": who, "note": note, "percent": _percent(steps_now),
                          "done": sum(1 for s in steps_now if s["status"] == "done"), "total": len(steps_now)}]
                        + (p.get("history") or []))[:KEEP_HISTORY]
        data[project] = p
        _save(data)
    s = _summary(project, p)
    return {"project": project, "percent": s["percent"], "done": s["done"], "total": s["total"], "updated": now}


@tool(name="progress", category="repo", doc="repo/progress.md")
def progress(project: str = "") -> dict:
    """How far each project's release has got, as its agent last reported.

    Args:
        project: one project's name for its full record (steps and history); empty for a summary of all.

    Returns:
        {projects: [{project, title, percent, done, total, metrics, waiting_on_owner: [{text, since}], agent, updated,
        note}]}, or for one project that summary plus steps and history (newest first).

    Raises:
        ProgressError: the named project has never been reported.
    """
    data = _load()
    if not project:
        rows = [_summary(slug, p) for slug, p in data.items()]
        return {"projects": sorted(rows, key=lambda r: -(r["updated"] or 0))}
    if project not in data:
        raise ProgressError(f"no progress reported for {project!r} yet")
    p = data[project]
    return {**_summary(project, p), "steps": p.get("steps") or [], "history": p.get("history") or []}
