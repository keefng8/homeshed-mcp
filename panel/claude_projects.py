"""Projects read on this PC, for the Projects page when no helper lists them (an end user, 2026-11-03: the panel's
helper-only pages showed nothing; the owner: "check the other features too, the same way"). The same rows the
helper's /projects gives: each folder a Claude Code chat started in is a project, with its chats (claude_sessions.py
reads them), whether one is working now, its owner (the newest typed-in chat's name), its git state and GitHub link,
its size on disk (measured in the background, never on the request), its key files and the files pinned for agents.

Only names are read from a project's files, never their contents; a git remote is shown without any user name or
token in it. Pins live in the panel's data folder.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import claude_sessions as cs

MAX_FILES = 600  # chat files read, newest first
MAX_PROJECTS = 60
SIZE_TTL_S = 15 * 60
MAX_ENTRIES = 400_000  # a walk that meets more stops there and says the size is at least that
GIT_TTL_S = 60
MAX_PINS = 30
KEY_FILES = ("CLAUDE.md", "AGENTS.md", "README.md", "ARCHITECTURE.md", ".claude/rules.md", ".claude/todo.md",
             "docs", "package.json", "pyproject.toml", "requirements.txt", "docker-compose.yml", "docker-compose.yaml",
             "Dockerfile")
_sizes: dict[str, dict] = {}
_measuring: set[str] = set()
_git_cache: dict[str, tuple[float, dict | None]] = {}
_lock = threading.Lock()


def _pins_file() -> Path:
    return Path(os.environ.get("PANEL_DATA_DIR") or Path.home() / ".homeshed-panel") / "project-pins.json"


def _key(folder: str) -> str:
    return folder.rstrip("\\/").replace("/", "\\").lower() if re.match(r"^[A-Za-z]:", folder) else folder.rstrip("/")


def folder_size(folder: str, limit: int = MAX_ENTRIES) -> dict:
    """Bytes and files under a folder, not following links. Stops after `limit` entries (partial: at least that)."""
    total = files = seen = 0
    stack = [folder]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    if seen > limit:
                        return {"bytes": total, "files": files, "partial": True}
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return {"bytes": total, "files": files, "partial": False}


def _measure(folder: str) -> None:
    try:
        r = folder_size(folder)
        r["at"] = time.time()
        _sizes[_key(folder)] = r
    finally:
        with _lock:
            _measuring.discard(_key(folder))


def size_of(folder: str, now: float) -> dict | None:
    """The last measured size (None until the first measure ends); an old or missing one is measured again in the
    background."""
    k = _key(folder)
    hit = _sizes.get(k)
    if not hit or now - hit["at"] > SIZE_TTL_S:
        with _lock:
            if k not in _measuring:
                _measuring.add(k)
                threading.Thread(target=_measure, args=(folder,), daemon=True, name="project-size").start()
    return hit


def github_url(remote: str) -> str | None:
    """https://github.com/owner/repo for a GitHub remote in any of its forms; credentials never survive."""
    m = re.match(r"^(?:git@github\.com:|ssh://git@github\.com/|https?://(?:[^@/]+@)?github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$",
                 remote.strip())
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None


def clean_remote(remote: str) -> str:
    return re.sub(r"^(https?://)[^@/]+@", r"\1", remote.strip())


def _run_git(folder: str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", folder, *args], capture_output=True, text=True, timeout=6,
                           encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def git_info(folder: str, now: float) -> dict | None:
    """Read-only git: branch, remote, changed files, ahead/behind, last commit. Cached a minute."""
    k = _key(folder)
    hit = _git_cache.get(k)
    if hit and now - hit[0] < GIT_TTL_S:
        return hit[1]
    top = _run_git(folder, "rev-parse", "--show-toplevel")
    info = None
    if top:
        remote = _run_git(folder, "remote", "get-url", "origin") or ""
        last = (_run_git(folder, "log", "-1", "--format=%h%x1f%s%x1f%ct") or "").split("\x1f")
        status = _run_git(folder, "status", "--porcelain")
        ab = (_run_git(folder, "rev-list", "--left-right", "--count", "@{upstream}...HEAD") or "").split()
        info = {"root": str(Path(top)), "branch": _run_git(folder, "branch", "--show-current") or "(detached)",
                "remote": clean_remote(remote) or None, "github": github_url(remote) if remote else None,
                "changed": len(status.splitlines()) if status is not None else None,
                "behind": int(ab[0]) if len(ab) == 2 else None, "ahead": int(ab[1]) if len(ab) == 2 else None,
                "last_commit": {"hash": last[0], "subject": last[1][:140], "at": int(last[2])} if len(last) == 3 else None}
    _git_cache[k] = (now, info)
    return info


def key_files(folder: str) -> list[dict]:
    return [{"path": rel, "dir": (Path(folder) / rel).is_dir()} for rel in KEY_FILES if (Path(folder) / rel).exists()]


def _load_pins() -> dict:
    try:
        d = json.loads(_pins_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_pins(d: dict) -> None:
    f = _pins_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def _name(info: dict, live: dict | None) -> str:
    here = (info["cwd"] or "").rstrip("\\/").lower()
    names = ((live or {}).get("name"), info["custom"], info["agent"], info["ai"])
    return next((n for n in names if n and n.rstrip("\\/").lower() != here), None) or Path(info["cwd"]).name or info["cwd"]


def list_projects(now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    open_now, groups = cs.running(), {}
    for path in cs._files()[:MAX_FILES]:
        try:
            st = path.stat()
        except OSError:
            continue
        info = cs.read_chat(path)
        if not info or not info["cwd"] or st.st_size < cs.MIN_BYTES:
            continue
        g = groups.setdefault(_key(info["cwd"]), {"folder": info["cwd"], "sessions": []})
        live = open_now.get(path.stem)
        g["sessions"].append({"id": path.stem, "name": _name(info, live)[:80], "title": (info["ai"] or "")[:120],
                              "last_active": st.st_mtime, "open": bool(live), "status": (live or {}).get("status"),
                              "kind": "you" if info["entry"] in (None, "cli") else "job"})
    pins, rows = _load_pins(), []
    for k, g in list(groups.items())[:MAX_PROJECTS]:
        folder, sess = g["folder"], g["sessions"]  # newest first
        exists = Path(folder).is_dir()
        owners = [s for s in sess if s["kind"] == "you"] or sess
        rows.append({
            "folder": folder, "name": Path(folder).name or folder, "exists": exists,
            "owner": owners[0]["name"], "agents": list(dict.fromkeys(s["name"] for s in sess))[:8],
            "sessions_total": len(sess), "sessions": sess[:6], "open": sum(s["open"] for s in sess),
            "busy": any(s["status"] == "busy" for s in sess), "last_active": sess[0]["last_active"],
            "size": size_of(folder, now) if exists else None,
            "git": git_info(folder, now) if exists else None,
            "key_files": key_files(folder) if exists else [],
            "pins": pins.get(k, []),
        })
    return rows


def listing() -> dict:
    return {"projects": list_projects(), "measuring": len(_measuring), "at": time.time()}


# Each refusal is its own class: the panel answers a fixed message for each, never the exception's text (CodeQL).
class NotAProject(LookupError):
    pass


class NotInProject(LookupError):
    pass


class PinOutside(ValueError):
    pass


class TooManyPins(ValueError):
    pass


def _project_folder(folder: str) -> str:
    k = _key(str(folder or ""))
    for p in list_projects():
        if _key(p["folder"]) == k:
            return p["folder"]
    raise NotAProject("not a project")


def _inside(folder: str, path: str) -> str:
    """The pinned path relative to the project folder; it must exist there (no escaping with .. or another drive)."""
    base = Path(folder).resolve()
    target = (base / str(path or "").strip()).resolve()
    if target != base and base not in target.parents:
        raise PinOutside("outside the project")
    if not target.exists():
        raise NotInProject("not found in the project")
    return str(target.relative_to(base)) or "."


def pin(folder: str, path: str, note: str) -> dict:
    """NotAProject, NotInProject, PinOutside or TooManyPins when it can't be pinned."""
    folder = _project_folder(folder)
    rel = _inside(folder, path)
    note = re.sub(r"\s+", " ", str(note or "")).strip()[:200]
    with _lock:
        d = _load_pins()
        items = [p for p in d.get(_key(folder), []) if p.get("path") != rel]
        if len(items) >= MAX_PINS:
            raise TooManyPins("too many pins")
        items.append({"path": rel, "dir": (Path(folder) / rel).is_dir(), "note": note, "at": time.time()})
        d[_key(folder)] = items
        _save_pins(d)
    return {"pins": items}


def unpin(folder: str, path: str) -> dict:
    k, rel = _key(str(folder or "")), str(path or "")
    with _lock:
        d = _load_pins()
        items = [p for p in d.get(k, []) if p.get("path") != rel]
        if items:
            d[k] = items
        else:
            d.pop(k, None)
        _save_pins(d)
    return {"pins": items}
