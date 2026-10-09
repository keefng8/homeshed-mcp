"""Per-tool on/off switches.

Two layers decide whether a tool may run:

1. **Default-off (release blocker #4).** Tools that change things (git writes, Docker start/stop/restart) and the
   code-execution tools (dev.*) are off on every install until the owner turns them on, with either
     - the environment: ENABLE_TOOLS=git.commit,git.push   or a group, git.*   ("*" = every default-off tool except
       dev.*, which needs "dev.*" or an exact id, because it runs code), or
     - the switch file's "enabled" list (written by set_enabled).
2. **The owner's switch file** (DISABLED_TOOLS_FILE): its "disabled" list switches any tool off, and always wins.

A missing file means no file overrides. An unreadable file also means no overrides (fail open, logged): a corrupt
switch file must never silently lock every tool, and it can't turn a default-off tool on either.
registry.py asks why_disabled() on every call and refuses with that message.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from paths import data_path

SWITCH_FILE = Path(os.environ.get("DISABLED_TOOLS_FILE") or data_path("usage/disabled_tools.json"))

# The 13 tools that ship switched off: the ones that change things or run code. Keep in step with the manifests.
DEFAULT_OFF = frozenset({
    "git.commit", "git.push", "git.pull", "git.clone", "git.branch",
    "docker.container.start", "docker.container.stop", "docker.container.restart",
    "dev.build", "dev.lint", "dev.node", "dev.python", "dev.test",
})
CODE_EXECUTION_GROUP = "dev"

_lock = threading.Lock()
_logger = logging.getLogger(__name__)
_EMPTY = {"disabled": frozenset(), "enabled": frozenset()}
_cache: tuple[object, dict] = (None, _EMPTY)  # (file signature, lists): is_disabled runs on every tool call
_warned: set[str] = set()


def _read() -> dict[str, set[str]]:
    """Current file lists. Costs one os.stat per call; the file is only re-read when it changes."""
    global _cache
    try:
        st = SWITCH_FILE.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        _cache = (None, _EMPTY)
        return {k: set(v) for k, v in _EMPTY.items()}
    except OSError:
        sig = "unreadable"
    if sig == _cache[0] and sig != "unreadable":
        return {k: set(v) for k, v in _cache[1].items()}
    try:
        data = json.loads(SWITCH_FILE.read_text(encoding="utf-8"))
        lists = {k: frozenset(str(x) for x in data.get(k, [])) for k in ("disabled", "enabled")}
    except (OSError, ValueError, AttributeError, TypeError):
        _logger.error("tool switch file %s is unreadable: no overrides apply (fail open)", SWITCH_FILE)
        lists = _EMPTY
    _cache = (sig, lists)
    return {k: set(v) for k, v in lists.items()}


def _env_enables(tool_id: str) -> bool:
    """Does ENABLE_TOOLS turn this default-off tool on? Read on every call, so a change applies at once."""
    group = tool_id.split(".", 1)[0]
    for token in (t.strip() for t in os.environ.get("ENABLE_TOOLS", "").split(",")):
        if not token:
            continue
        if token == tool_id or token == f"{group}.*":
            return True
        if token == "*" and group != CODE_EXECUTION_GROUP:
            return True
        if token != "*" and not token.endswith(".*") and token not in DEFAULT_OFF and token not in _warned:
            _warned.add(token)
            _logger.warning("ENABLE_TOOLS names %r, which isn't a default-off tool: ignored", token)
    return False


def why_disabled(tool_id: str) -> str | None:
    """None if the tool may run; otherwise a message that tells the user exactly how to turn it on."""
    with _lock:
        lists = _read()
    if tool_id in lists["disabled"]:
        return f"{tool_id} is switched off by the owner of this server."
    if tool_id in DEFAULT_OFF and tool_id not in lists["enabled"] and not _env_enables(tool_id):
        extra = " It runs code, so only turn it on in a disposable environment." if tool_id.startswith("dev.") else ""
        return f"{tool_id} is off by default because it changes things. To use it, set ENABLE_TOOLS={tool_id}.{extra}"
    return None


def is_disabled(tool_id: str) -> bool:
    return why_disabled(tool_id) is not None


def disabled() -> list[str]:
    """Sorted ids switched off in the file (the owner's explicit switches)."""
    with _lock:
        return sorted(_read()["disabled"])


def set_enabled(tool_id: str, enabled: bool) -> bool:
    """Switch one tool on or off in the file. Returns True if the file changed. Atomic write.
    On: removed from "disabled", and added to "enabled" if it's a default-off tool. Off: the reverse."""
    global _cache
    with _lock:
        lists = _read()
        before = {k: set(v) for k, v in lists.items()}
        if enabled:
            lists["disabled"].discard(tool_id)
            if tool_id in DEFAULT_OFF:
                lists["enabled"].add(tool_id)
        else:
            lists["disabled"].add(tool_id)
            lists["enabled"].discard(tool_id)
        if lists == before:
            return False
        SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SWITCH_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: sorted(v) for k, v in lists.items()}, indent=1), encoding="utf-8")
        os.replace(tmp, SWITCH_FILE)
        # Refresh the cache with what was just written: switching a tool on and back off writes two files of the same
        # size, and within one clock tick the (mtime, size) signature can't tell them apart (a flaky test, 2026-09-29).
        try:
            st = SWITCH_FILE.stat()
            _cache = ((st.st_mtime_ns, st.st_size), {k: frozenset(v) for k, v in lists.items()})
        except OSError:
            _cache = (None, _EMPTY)
        return True
