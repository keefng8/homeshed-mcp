"""Which memory each project uses. Every Claude Code session on one machine reaches the tool server with the owner
token, so without this every project's memory.* calls would land in the shared agent, and a project's own memory agent
would stay empty.

A session now says which folder it works in: the memory tools' `project_dir` argument, or an X-Homelab-Project header
from its connection settings. This registry maps that folder to the project's own memory agent. The longest match
wins, so D:/Work/research beats D:/Work; a folder that matches nothing keeps the shared agent.
Only the owner's calls use it: a client token stays with its own agent (clients.memory_agent_for), so an app can never
read another project's memory by naming its folder.

MCP's Roots feature would have told us the folder without asking, but Claude Code speaks the stateless 2026-07-28
protocol (no back-channel for server requests), and that revision deprecates Roots in favour of "tool parameters,
resource URIs, or server configuration": the two ways used here.

File (PROJECTS_FILE): {"projects": [{"path": "C:/Users/you/my-app", "name": "my-app", "agent": "agt-..."}]}
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import unquote

import clients
from paths import data_path

PROJECTS_FILE = Path(os.environ.get("PROJECTS_FILE") or data_path("usage/projects.json"))
HEADER = "x-homelab-project"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
DIR_HELP = ("Your primary working directory, as shown in your environment (for example C:\\Users\\you\\my-app or "
            "~/my-app). Memory is kept per project: with it you read and write your project's own memory; without it, "
            "the shared one.")

# Set by server.py from the X-Homelab-Project header for the duration of an owner's tool call.
current_dir: contextvars.ContextVar[str] = contextvars.ContextVar("current_project_dir", default="")

_lock = threading.Lock()
_cache: tuple[object, list] = (None, [])


class ProjectError(ValueError):
    """A bad registry request. The message is safe to show in an admin client."""


def norm(path: str) -> str:
    """A folder as one comparable string: forward slashes, no trailing slash, case folded (Windows paths ignore case).
    Accepts file:// URIs. Anything that isn't an absolute path gives ""."""
    p = unquote(str(path or "").strip())
    if p.lower().startswith("file://"):
        p = p[7:]
        if re.match(r"^/[A-Za-z]:", p):  # file:///D:/x
            p = p[1:]
    p = p.replace("\\", "/").rstrip("/")
    if re.fullmatch(r"[A-Za-z]:", p):
        p += "/"
    return p.casefold() if re.match(r"^([A-Za-z]:/|/)", p) else ""


def _read() -> tuple[list[dict], bool]:
    """(valid rows, readable). A missing file is readable and empty."""
    global _cache
    try:
        st = PROJECTS_FILE.stat()
        sig = (str(PROJECTS_FILE), st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return [], True
    except OSError:
        return [], False
    if sig == _cache[0]:
        return _cache[1], True
    try:
        data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        rows = [r for r in data["projects"] if isinstance(r, dict) and norm(r.get("path"))
                and clients.AGENT_RE.match(str(r.get("agent", "")))]
    except (OSError, ValueError, KeyError, TypeError):
        return [], False  # unreadable: every session keeps the shared agent, as before this module
    _cache = (sig, rows)
    return rows, True


def find(project_dir: str) -> dict | None:
    """The registered project that holds this folder (the longest registered path wins), or None."""
    want = norm(project_dir)
    if not want:
        return None
    best, best_len = None, -1
    for row in _read()[0]:
        base = norm(row["path"])
        inside = want == base or want.startswith(base if base.endswith("/") else base + "/")
        if inside and len(base) > best_len:
            best, best_len = row, len(base)
    return best


def agent_for(project_dir: str) -> str | None:
    row = find(project_dir)
    return row["agent"] if row else None


@contextlib.contextmanager
def working_in(project_dir: str):
    """For a memory tool that calls others: its project_dir argument names the project for the whole call, nested
    calls included. Empty keeps what the connection said (the X-Homelab-Project header)."""
    token = current_dir.set(project_dir) if project_dir else None
    try:
        yield
    finally:
        if token is not None:
            current_dir.reset(token)


def list_projects() -> dict:
    rows, readable = _read()
    return {"projects": [{"path": r["path"], "name": r.get("name") or "", "agent": r["agent"]} for r in rows],
            "unreadable": not readable}


def _editable() -> list[dict]:
    rows, readable = _read()
    if not readable:
        raise ProjectError("the projects file is unreadable; fix or remove it before changing projects")
    return list(rows)


def _save(rows: list[dict]) -> None:
    global _cache
    PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROJECTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"projects": rows}, indent=1), encoding="utf-8")
    os.replace(tmp, PROJECTS_FILE)
    _cache = (None, [])  # re-read next time: a same-size rewrite inside one clock tick keeps (mtime, size) unchanged


def register(path: str, name: str, agent: str) -> dict:
    """Add a project, or replace the one at the same folder."""
    shown = str(path or "").strip().replace("\\", "/").rstrip("/")
    if not norm(shown):
        raise ProjectError("path must be an absolute folder, like C:/Users/you/my-app")
    if not NAME_RE.match(str(name or "")):
        raise ProjectError("name: 1-40 lowercase letters, digits or hyphens, like my-app")
    if not clients.AGENT_RE.match(str(agent or "")):
        raise ProjectError("a memory agent id looks like agt-xxxxxxxxxx (bootstrap-new-project.ps1 prints it)")
    row = {"path": shown, "name": name, "agent": agent}
    with _lock:
        _save([r for r in _editable() if norm(r["path"]) != norm(shown)] + [row])
    return row


def remove(path: str) -> bool:
    with _lock:
        rows = _editable()
        keep = [r for r in rows if norm(r["path"]) != norm(path)]
        if len(keep) == len(rows):
            return False
        _save(keep)
    return True
