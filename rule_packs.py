"""Rule packs on this machine (2026-10-01): what's installed, what HomeShed Pro offers, install / add / check / remove,
and switching the guard hook on or off in Claude Code. The command line is `homeshed-mcp packs ...` (cli.py).

Packs are data files in DATA_DIR/packs. A copy of guard_engine.py, next to them, is the Claude Code hook that reads them
before each command or file write. It runs with the base Python, so an upgrade or a uv cache clean can't break it
(the prepper's review). Installing a pack never touches Claude Code's settings: `packs on` does, once, after showing the
change and asking. It backs the file up first (the first backup is kept), changes only its own entry, and never edits
a settings file it can't read: then it prints the entry to paste. A Pro pack comes from the Pro website with this
install's Pro key (mavis_pro.py), or on a Docker/HTTP install through the server's /pro routes with the owner token.
Every pack is fully validated before it's saved (guard_engine.validate_pack: data only, every example proven). If Pro
ends, installed packs keep working.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import guard_engine
from paths import data_path

PACKS = Path(data_path("packs"))
MODES = Path(data_path("guard-modes.json"))
ENGINE_COPY = Path(data_path("guard_engine.py"))
# The tools a guard can watch. Claude Code tests a matcher like this one as an unanchored JavaScript regex
# (code.claude.com/docs/en/hooks, checked 2026-10-01), so it's anchored: "Bash" alone would also match BashOutput.
MATCHER = "^(Bash|PowerShell|Write|Edit|NotebookEdit|SendMessage|Agent|Read)$|^mcp__"
EVENTS = guard_engine.HOOK_EVENTS  # tool calls, prompts, session starts and turn ends (the built-in checks need them)
# A stuck check costs at most this, then Claude Code carries on: "A timed-out command ... hook doesn't block the tool
# call" (code.claude.com/docs/en/hooks, checked 2026-10-01). Without it the default is 600 s.
HOOK_TIMEOUT_S = 10
MARKER = "--homeshed-hook"  # names our entry in Claude Code's settings, so on/off only ever touch it (R&D's review)
TRIAL_S = 5.0               # the timed trial's base limit, plus TRIAL_EACH_S per guard: only a backstop for a pattern
                            # that never finishes (the trial judges growth, not time), so slow PCs get room


class PackError(RuntimeError):
    """A message fit to show as it is."""


def claude_settings() -> Path:
    """Claude Code's user settings, where the hook goes (CLAUDE_SETTINGS overrides it, for tests)."""
    return Path(os.environ.get("CLAUDE_SETTINGS") or Path.home() / ".claude" / "settings.json")


# --- packs -------------------------------------------------------------------------------------------------------------
def session_notes(pack: dict) -> list[str]:
    """What a pack's session-note guards add to Claude's context at the start of every session."""
    return [str(g.get("message") or "") for g in pack.get("guards") or []
            if isinstance(g, dict) and g.get("kind") == "builtin" and g.get("check") == "session-note"]


def installed() -> list[dict]:
    """Each installed pack, with where it came from as the engine sees it (HomeShed Pro, added from a file, or
    unverified) and the session notes it adds, so the user can see what Claude is told every session."""
    return [{**{k: p.get(k) for k in ("slug", "title", "version", "tier", "summary")},
             "source": guard_engine.SOURCES.get(p.get("_source"), "unverified"),
             "notes": session_notes(p), "notes_on": bool(p.get("_notes_ok"))}
            for p in guard_engine.load_packs(PACKS)]


TRIAL_EACH_S = 3.0


def _index_write(update) -> None:
    """Change the installer's index (packs/.installed.json) atomically. It's the only record of where each pack came
    from: the engine never trusts a pack's own word for it (R&D's review)."""
    index = guard_engine.read_index(PACKS)
    update(index)
    tmp = PACKS / ".installed.tmp"
    tmp.write_text(json.dumps(index, indent=1), encoding="utf-8")
    tmp.replace(PACKS / guard_engine.INDEX)


def _save(pack: dict, source: str, notes_ok: bool = False) -> dict:
    """Saved atomically, then recorded in the index with its hash and where it came from (pro or file), which the engine
    labels its messages with. notes_ok: the user read and confirmed its session notes (a file pack's need that)."""
    PACKS.mkdir(parents=True, exist_ok=True)
    pack = {k: v for k, v in pack.items() if not str(k).startswith("_")}  # nothing the engine adds is ever saved
    raw = json.dumps(pack, indent=1).encode("utf-8")
    tmp = PACKS / f".{pack['slug']}.tmp"  # not *.json, so the engine never reads a half-written pack
    tmp.write_bytes(raw)
    tmp.replace(PACKS / f"{pack['slug']}.json")
    entry = {"source": source, "sha256": guard_engine.file_hash(raw), **({"notes_ok": True} if notes_ok else {})}
    _index_write(lambda index: index.__setitem__(pack["slug"], entry))
    return {"installed": {k: pack[k] for k in ("slug", "title", "version", "tier")}, "hook_on": hook_state()["on"],
            "notes": session_notes(pack), "notes_on": source == "pro" or notes_ok}


def timed_trial(pack: dict) -> list[str]:
    """Problems found by running every pattern against long, hostile input in a child process with a time limit
    (R&D's review): a pattern that never finishes is refused here, before it could cost a tool call anything."""
    limit = min(90.0, TRIAL_S + TRIAL_EACH_S * len(pack.get("guards") or []))
    try:
        r = subprocess.run([sys.executable, "-I", "-S", guard_engine.__file__, "--trial"], input=json.dumps(pack),
                           capture_output=True, text=True, timeout=limit)
        slow = json.loads(r.stdout or "{}").get("slow")
    except subprocess.TimeoutExpired:
        return [f"a check took over {limit:g} s on long input: one of its patterns can run for ever"]
    except (OSError, ValueError):
        return ["its patterns couldn't be timed"]
    if not isinstance(slow, list):
        return ["its patterns couldn't be timed"]
    return [f"{gid}: its patterns are slow on long input" for gid in slow]


def _checked(pack, slug: str | None = None) -> dict:
    problems = guard_engine.validate_pack(pack) if pack else ["it arrived empty"]
    if not problems and slug and pack.get("slug") != slug:
        problems = ["its name doesn't match the pack asked for"]
    if not problems:
        problems = timed_trial(pack)
    if problems:
        raise PackError("That pack failed its checks, so nothing was installed: " + "; ".join(problems[:3]))
    return pack


_LOCAL_NAMES = (".local", ".lan", ".home", ".internal", ".localhost")
_ONE_WORD = re.compile(r"[a-z_][a-z0-9_-]*")


def address_problem(url: str) -> str:
    """Why this address isn't safe to send an owner token or a Pro key to, or "" when it is (R&D's security review,
    2026-10-01, confirmed high: the token went to any URL). https; plain http only to this machine or your own network:
    localhost, a private or loopback IP, a one-word name (a Docker service) or a .local/.lan/.home/.internal name. No
    user:password, query or fragment. Also used for the Pro website's address (mavis_pro.base_url)."""
    import ipaddress
    from urllib.parse import urlsplit
    try:
        u = urlsplit(str(url or "").strip())
        u.port  # noqa: B018 - a bad port raises here
    except ValueError:
        return "That address isn't a valid URL."
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host or u.username or u.password or u.query or u.fragment:
        return "Give the address as http(s)://host:port, with nothing else in it."
    if u.scheme == "http":
        try:
            ip = ipaddress.ip_address(host)
            if ip.version == 6 and ip.ipv4_mapped:  # ::ffff:8.8.8.8 is 8.8.8.8, whatever this Python calls private
                ip = ip.ipv4_mapped
            local = ip.is_loopback or ip.is_private
        except ValueError:
            # A one-word name must look like one (a Docker service): 134744072 or 0x08080808 is a public IP in disguise.
            local = host == "localhost" or host.endswith(_LOCAL_NAMES) or bool(_ONE_WORD.fullmatch(host))
        if not local:
            return (f"{host} isn't on this machine or your own network, so it needs https: plain http would send your "
                    "token where anyone on the way can read it.")
    return ""


def server_url(server: str) -> str:
    """A HomeShed server address that's safe to send the owner token or a Pro key to (address_problem), or PackError."""
    if problem := address_problem(server):
        raise PackError(problem)
    from urllib.parse import urlsplit
    u = urlsplit(str(server).strip())
    return f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"


def _server_get(server: str, token: str, path: str) -> dict:
    """A HomeShed server's /pro route (a Docker or HTTP install), with the owner token: only to an address that's safe
    to send it to (server_url)."""
    import httpx
    server = server_url(server)
    try:
        r = httpx.get(server + path, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    except httpx.HTTPError:
        raise PackError(f"HomeShed isn't answering at {server}.") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200:
        raise PackError((body.get("error") if isinstance(body, dict) else None) or f"HomeShed answered HTTP {r.status_code}.")
    return body if isinstance(body, dict) else {}


def offered(server: str | None = None, token: str | None = None) -> dict:
    """The packs HomeShed Pro offers: {configured, available: [{slug, title, description}], error}."""
    if server:
        try:
            return _server_get(server, token or "", "/pro/packs")
        except PackError as exc:
            return {"configured": False, "available": [], "error": str(exc)}
    import mavis_pro
    return mavis_pro.packs()


def install(slug: str, server: str | None = None, token: str | None = None, confirm_notes=None) -> dict:
    """A Pro pack, fetched with this install's Pro key (or through a HomeShed server), checked, then saved. Through a
    server it's labelled so, and its session notes, like a file pack's, wait for confirm_notes(notes) to say yes:
    nothing yet proves such a pack came from the Pro site unchanged."""
    if not guard_engine.SLUG.match(str(slug or "")):
        raise PackError("That isn't a pack name.")
    if server:
        pack = _checked(_server_get(server, token or "", f"/pro/packs/{slug}").get("pack"), slug)
        notes = session_notes(pack)
        return _save(pack, "server", notes_ok=bool(notes and confirm_notes and confirm_notes(notes)))
    else:
        import mavis_pro
        try:
            pack = mavis_pro.pack(slug)
        except mavis_pro.MavisProError as exc:
            raise PackError(str(exc)) from None
    return _save(_checked(pack, slug), "pro")


def _read_pack(file) -> dict:
    path = Path(file)
    try:
        if path.stat().st_size > guard_engine.MAX_PACK_BYTES:
            raise PackError(f"{file} is over {guard_engine.MAX_PACK_BYTES // 1000} KB: too big to be a pack.")
        return guard_engine.strict_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackError(f"Couldn't read {file} as JSON ({exc}).") from None
    except RecursionError:
        raise PackError(f"Couldn't read {file} as JSON (it's nested too deeply).") from None


def check(file) -> list[str]:
    """Problems with your own pack file, empty when it's good (nothing is installed)."""
    try:
        pack = _read_pack(file)
    except PackError as exc:
        return [str(exc)]
    return guard_engine.validate_pack(pack) or timed_trial(pack)


def add(file, confirm_notes=None) -> dict:
    """A pack from a file (your own, or one someone gave you): checked like a Pro pack, then installed. Free: the engine
    is the free part. If it adds a session note, Claude reads that note only once confirm_notes(notes) says yes: the
    user sees exactly what Claude would be told every session (R&D's review)."""
    pack = _checked(_read_pack(file))
    notes = session_notes(pack)
    return _save(pack, "file", notes_ok=bool(notes and confirm_notes and confirm_notes(notes)))


def remove(slug: str) -> dict:
    path = PACKS / f"{slug}.json"
    if not guard_engine.SLUG.match(str(slug or "")) or not path.is_file():
        raise PackError("That pack isn't installed here (homeshed-mcp packs list shows what is).")
    path.unlink()
    _index_write(lambda index: index.pop(slug, None))
    return {"removed": slug}


# --- the hook in Claude Code -------------------------------------------------------------------------------------------
def python_for_hook() -> str:
    """The base interpreter, not a virtual environment's, which an upgrade or a uv cache clean can remove."""
    return Path(getattr(sys, "_base_executable", None) or sys.executable).as_posix()


def git_bash() -> str | None:
    """Git Bash, which Claude Code runs Windows hooks with when it's installed (CLAUDE_CODE_GIT_BASH_PATH, or next to
    git). Not any bash.exe on PATH: Windows' own one starts WSL."""
    named = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if named and Path(named).is_file():
        return named
    git = shutil.which("git")
    if git:
        for base in Path(git).resolve().parents[:3]:
            bash = base / "bin" / "bash.exe"
            if bash.is_file():
                return str(bash)
    return None


def hook_command() -> str:
    """The command Claude Code runs. Windows without Git Bash runs hooks in PowerShell (code.claude.com/docs/en/hooks),
    where a quoted program needs the call operator; bash and Git Bash take it as it is. Paths are always quoted, so a
    user name with a space is fine."""
    command = (f'"{python_for_hook()}" -I -S "{ENGINE_COPY.as_posix()}" --packs "{PACKS.as_posix()}" '
               f'--modes "{MODES.as_posix()}" {MARKER}')
    return "& " + command if _powershell() else command


def _powershell() -> bool:
    return sys.platform == "win32" and git_bash() is None


def hook_entries() -> dict[str, dict]:
    """One entry per event, all running the same engine copy: before a tool call (only the tools a guard can watch),
    when a prompt arrives, when a session starts or compacts, and when a turn ends."""
    def hook() -> dict:
        h = {"type": "command", "command": hook_command(), "timeout": HOOK_TIMEOUT_S}
        if _powershell():
            h["shell"] = "powershell"
        return h
    return {event: {"matcher": MATCHER, "hooks": [hook()]} if event == "PreToolUse" else {"hooks": [hook()]}
            for event in EVENTS}


def hook_entry() -> dict:
    """The tool-call entry, the one every rule pack needs."""
    return hook_entries()["PreToolUse"]


def _ours(entry) -> bool:
    return isinstance(entry, dict) and any(isinstance(h, dict) and MARKER in str(h.get("command") or "").split()
                                           for h in entry.get("hooks") or [])


def _read_settings(path: Path) -> tuple[dict | None, str]:
    """(Claude Code's settings, a hash of the file as read). The settings are {} when there's no file yet, or None when
    they can't be read safely: then nothing is edited and the entry is printed to paste instead (the prepper's review)."""
    if not path.exists():
        return {}, ""
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None, ""
    if not isinstance(data, dict):
        return None, ""
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict) or not all(isinstance(hooks.get(event, []), list) for event in EVENTS):
        return None, ""
    return data, hashlib.sha256(raw).hexdigest()


def _ours_by_event(data: dict) -> dict[str, list]:
    hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
    return {event: [e for e in hooks.get(event, []) if _ours(e)] for event in EVENTS}


def _write_settings(path: Path, data: dict, read_hash: str) -> bool:
    """Write atomically, and only if the file is still the one that was read (Claude Code or the user may have changed
    it meanwhile: R&D's review). False when it changed: nothing is written."""
    try:
        now = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
    except OSError:
        return False
    if now != read_hash:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".homeshed-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return True


def copy_engine() -> None:
    PACKS.mkdir(parents=True, exist_ok=True)
    tmp = ENGINE_COPY.with_suffix(".tmp")
    shutil.copyfile(guard_engine.__file__, tmp)
    tmp.replace(ENGINE_COPY)


def turn_on(confirm=None, settings: Path | None = None) -> dict:
    """Copy the engine next to the packs, then add the hook to Claude Code, one entry per event, if confirm(entries,
    path) says yes (no confirm: yes). {changed, reason, entries, path, backup}."""
    path = settings or claude_settings()
    copy_engine()
    entries = hook_entries()
    out = {"changed": False, "entries": entries, "path": str(path), "backup": None}
    data, read_hash = _read_settings(path)
    if data is None:
        return {**out, "reason": "unreadable"}
    if _ours_by_event(data) == {event: [entries[event]] for event in EVENTS}:
        return {**out, "reason": "already on"}
    if confirm is not None and not confirm(entries, path):
        return {**out, "reason": "declined"}
    backup = path.with_name(path.name + ".homeshed-backup")
    if path.exists() and not backup.exists():
        shutil.copyfile(path, backup)  # the first backup is kept: it's the file from before HomeShed ever touched it
    hooks = data.setdefault("hooks", {})
    for event in EVENTS:
        hooks[event] = [e for e in hooks.get(event, []) if not _ours(e)] + [entries[event]]
    if not _write_settings(path, data, read_hash):
        return {**out, "reason": "changed meanwhile"}
    return {**out, "changed": True, "reason": "on", "backup": str(backup) if backup.exists() else None}


def turn_off(settings: Path | None = None) -> dict:
    """Remove this hook's own entries from Claude Code's settings; every other hook stays."""
    path = settings or claude_settings()
    data, read_hash = _read_settings(path)
    if data is None:
        return {"changed": False, "reason": "unreadable", "path": str(path)}
    if not any(_ours_by_event(data).values()):
        return {"changed": False, "reason": "already off", "path": str(path)}
    hooks = data["hooks"]
    for event in EVENTS:
        if event in hooks:
            hooks[event] = [e for e in hooks[event] if not _ours(e)]
            if not hooks[event]:
                del hooks[event]
    if not hooks:
        del data["hooks"]
    if not _write_settings(path, data, read_hash):
        return {"changed": False, "reason": "changed meanwhile", "path": str(path)}
    return {"changed": True, "reason": "off", "path": str(path)}


def hook_state(settings: Path | None = None) -> dict:
    """For doctor and `packs list`: is the hook on, and current (this install's command on every event, an up-to-date
    engine copy, a Python that still exists)? {on, current, why}."""
    path = settings or claude_settings()
    ours = _ours_by_event(_read_settings(path)[0] or {})
    if not any(ours.values()):
        return {"on": False, "current": False, "why": "off"}
    try:
        found = re.search(r'^ENGINE_VERSION = "([^"]+)"', ENGINE_COPY.read_text(encoding="utf-8"), re.M)
    except OSError:
        found = None
    first = next(entries[-1] for entries in ours.values() if entries)
    command = str(((first.get("hooks") or [{}])[0] or {}).get("command") or "")
    python = command.split('"')[1] if '"' in command else ""
    if not found or found.group(1) != guard_engine.ENGINE_VERSION:
        return {"on": True, "current": False, "why": "the engine copy is missing or older than this HomeShed"}
    if not python or not Path(python).exists():
        return {"on": True, "current": False, "why": "the Python the hook names no longer exists"}
    expected = hook_entries()
    if any(ours[event] != [expected[event]] for event in EVENTS):
        return {"on": True, "current": False, "why": "the hook is from an older HomeShed or another install"}
    return {"on": True, "current": True, "why": "on"}
