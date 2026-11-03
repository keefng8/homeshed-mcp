"""Past Claude Code chats read on this PC, for "Pick up where you left off" when no helper service lists them (an end
user, 2026-11-03: the list was always empty). The panel a person installs runs on their own PC (127.0.0.1), the same
one Claude Code runs on, so it can read Claude Code's own files directly:

- ~/.claude/projects/<folder>/<session id>.jsonl: one per chat. Only the first 64 KB and last 512 KB are read, for the
  folder it ran in and its names (the /rename title, the agent name, Claude Code's own title). What was said is
  never returned.
- ~/.claude/sessions/<pid>.json: the chats open now. Only the id, name and status are read; never the .key files.

Resume here gives the command to run (`claude --resume <id>` in the chat's folder) rather than starting a program:
the panel never runs anything on the person's PC by itself. Hiding a chat stores only its id, in the panel's data
folder; the chat's own file is never touched.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HEAD_BYTES, TAIL_BYTES = 64 * 1024, 512 * 1024
MIN_BYTES = 4 * 1024  # smaller than this, the chat never got going
HIDDEN_KEEP = 2000
_cache: dict[str, tuple[float, int, dict]] = {}
_lock = threading.Lock()


def projects_dir() -> Path:
    return CLAUDE_DIR / "projects"


def available() -> bool:
    """Whether Claude Code's chats are on this PC (the panel and Claude Code on the same PC, as user)."""
    return projects_dir().is_dir()


def _hidden_file() -> Path:
    return Path(os.environ.get("PANEL_DATA_DIR") or Path.home() / ".homeshed-panel") / "sessions-hidden.json"


def _records(chunk: bytes, skip_first: bool):
    lines = chunk.split(b"\n")
    for line in lines[1:] if skip_first else lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            yield d


def read_chat(path: Path) -> dict | None:
    """The folder, names and kind of one chat file. Cached by size and time."""
    try:
        st = path.stat()
    except OSError:
        return None
    hit = _cache.get(str(path))
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    info: dict = {"cwd": None, "custom": None, "agent": None, "ai": None, "entry": None, "continued": False}
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_BYTES)
            tail = b""
            if st.st_size > HEAD_BYTES:
                f.seek(max(HEAD_BYTES, st.st_size - TAIL_BYTES))
                tail = f.read(TAIL_BYTES)
    except OSError:
        return None
    for chunk, skip in ((head, False), (tail, True)):  # later records win: the tail is read last
        for d in _records(chunk, skip):
            t = d.get("type")
            if isinstance(d.get("cwd"), str) and not info["cwd"]:
                info["cwd"] = d["cwd"]
            if isinstance(d.get("entrypoint"), str) and not info["entry"]:
                info["entry"] = d["entrypoint"]
            if t == "custom-title" and isinstance(d.get("customTitle"), str):
                info["custom"] = d["customTitle"]
            elif t == "agent-name" and isinstance(d.get("agentName"), str):
                info["agent"] = d["agentName"]
            elif t == "ai-title" and isinstance(d.get("aiTitle"), str):
                info["ai"] = d["aiTitle"]
            elif t == "continued-in":
                info["continued"] = True
    _cache[str(path)] = (st.st_mtime, st.st_size, info)
    return info


def _alive(pid: int) -> bool:
    """Whether a process is running. Never os.kill on Windows: there it ends the process."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def running() -> dict[str, dict]:
    out = {}
    for f in (CLAUDE_DIR / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid, pid = d.get("sessionId"), d.get("pid")
        if isinstance(sid, str) and isinstance(pid, int) and _alive(pid):
            out[sid] = {"name": d.get("name") if isinstance(d.get("name"), str) else None,
                        "status": d.get("status") if d.get("status") in ("idle", "busy") else None}
    return out


def _files() -> list[Path]:
    try:
        files = [p for p in projects_dir().glob("*/*.jsonl") if ID_RE.match(p.stem)]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def hidden_ids() -> list[str]:
    try:
        ids = json.loads(_hidden_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [i for i in ids if isinstance(i, str) and ID_RE.match(i)] if isinstance(ids, list) else []


def find(sid: str) -> Path | None:
    hits = list(projects_dir().glob(f"*/{sid}.jsonl")) if ID_RE.match(sid or "") else []
    return hits[0] if len(hits) == 1 else None


class NotAChat(ValueError):
    """Not a chat on this PC. The panel answers its own fixed message (never the exception's text: CodeQL)."""


class ChatOpen(ValueError):
    """The chat is open in a window now."""


def set_hidden(sid: str, hide: bool) -> dict:
    """Hide or show one chat on the list. Only the id is stored. NotAChat: not a chat on this PC."""
    if not ID_RE.match(sid or ""):
        raise NotAChat("not a session id")
    with _lock:
        ids = [i for i in hidden_ids() if i != sid]
        if hide:
            if not find(sid):
                raise NotAChat("no such chat")
            ids = (ids + [sid])[-HIDDEN_KEEP:]
        f = _hidden_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(ids), encoding="utf-8")
        os.replace(tmp, f)
    return {"id": sid, "hidden": hide}


def list_chats(limit: int = 30, hidden: bool = False) -> list[dict]:
    """The newest typed-in chats (not scripts or the SDK), the same rows the helper's /sessions gives."""
    open_now, rows, gone = running(), [], set(hidden_ids())
    for path in _files():
        if len(rows) >= limit:
            break
        if (path.stem in gone) != hidden:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        info = read_chat(path)
        if (not info or st.st_size < MIN_BYTES or info["continued"] or not info["cwd"]
                or info["entry"] not in (None, "cli")):
            continue
        sid, live = path.stem, open_now.get(path.stem)
        here = info["cwd"].rstrip("\\/").lower()  # a chat never named goes by its folder: say something better
        names = ((live or {}).get("name"), info["custom"], info["agent"], info["ai"])
        name = next((n for n in names if n and n.rstrip("\\/").lower() != here), None) or Path(info["cwd"]).name or info["cwd"]
        rows.append({"id": sid, "name": name[:80], "title": (info["ai"] or "")[:120], "folder": info["cwd"],
                     "folder_ok": Path(info["cwd"]).is_dir(), "last_active": st.st_mtime, "open": bool(live),
                     "status": (live or {}).get("status")})
    return rows


def listing(limit: int = 30, hidden: bool = False) -> dict:
    """What GET /api/claude/sessions answers: the helper's shape, with resume as a command to copy."""
    rows = list_chats(limit, hidden)
    return {"sessions": rows, "resume_on": True, "can_resume": True, "resume_mode": "command",
            "hidden_count": len(hidden_ids()) if not hidden else len(rows)}


def resume_command(sid: str) -> dict:
    """The command that reopens one chat where it left off. NotAChat: not on this PC; ChatOpen: it's open now."""
    path = find(sid)
    info = read_chat(path) if path else None
    if not info or not info["cwd"]:
        raise NotAChat("no such chat")
    if sid in running():
        raise ChatOpen("open now")
    folder = info["cwd"]
    if re.search(r"[\"'`$;&|<>\r\n]", folder):  # a folder name that could break out of the quotes: no cd in it
        return {"command": f"claude --resume {sid}", "folder": folder, "folder_ok": Path(folder).is_dir(),
                "cd_first": True}
    if sys.platform == "win32":
        cmd = f'cd "{folder}"; claude --resume {sid}'  # PowerShell, Windows' own terminal
    else:
        cmd = f"cd '{folder}' && claude --resume {sid}"
    return {"command": cmd, "folder": folder, "folder_ok": Path(folder).is_dir()}
