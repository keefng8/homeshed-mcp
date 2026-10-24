"""Stray one-off commands in this container: stopped when they hog the CPU, with a push to the owner's phone.

A command a session runs here with `docker exec` (a quick lookup, a one-off write) carries on after the session gives
up on it: on 24 Oct 2026 a search over the whole disk held a core at 100% for 45 minutes with nobody at the desk.
Such a command has no parent inside the container (its parent pid is 0, and it isn't pid 1, the server), so this
never touches the server or anything the server started. One that has used most of a core for BUSY_FOR seconds in a
row is stopped (TERM, then KILL) and the owner is told what it was. Setting: stray_command_stop (default on).
"""
from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

PROC = Path("/proc")
CHECK_S, BUSY_FOR, BUSY_SHARE, GRACE_S = 60, 600, 0.8, 5
TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100  # /proc counts CPU time in these
_started = False
_lock = threading.Lock()


def _read(pid: int) -> tuple[int, float, str] | None:
    """(parent pid, CPU seconds used, command line) of a process, or None if it's gone."""
    try:
        stat = (PROC / str(pid) / "stat").read_text()
        cmd = (PROC / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        return None
    fields = stat.rsplit(")", 1)[1].split()  # after the name, which may hold spaces: state is fields[0]
    if fields[0] == "Z":
        return None  # already finished: uses nothing
    return int(fields[1]), (int(fields[11]) + int(fields[12])) / TICKS, cmd


def strays() -> dict[int, tuple[float, str]]:
    """{pid: (CPU seconds, command)} for every process a `docker exec` started, and everything under one."""
    procs = {int(p.name): r for p in PROC.iterdir() if p.name.isdigit() and (r := _read(int(p.name)))}
    roots = {pid for pid, (ppid, _, _) in procs.items() if ppid == 0 and pid != 1}
    out, todo = {}, list(roots)
    while todo:
        pid = todo.pop()
        out[pid] = procs[pid][1:]
        todo += [c for c, (ppid, _, _) in procs.items() if ppid == pid and c not in out]
    return out


def step(seen: dict, now_cpu: dict[int, tuple[float, str]], interval: float) -> list[tuple[int, str]]:
    """Move the busy counts on by one check; the (pid, command) of each one to stop. seen: {pid: [cpu, busy_s]}."""
    stop = []
    for pid in list(seen):
        if pid not in now_cpu:
            del seen[pid]
    for pid, (cpu, cmd) in now_cpu.items():
        last = seen.get(pid)
        busy = last is not None and (cpu - last[0]) >= BUSY_SHARE * interval
        seen[pid] = [cpu, (last[1] + interval) if busy else 0]
        if seen[pid][1] >= BUSY_FOR:
            stop.append((pid, cmd))
    return stop


def _stop(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(GRACE_S)
        if _read(pid):
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _tell(cmd: str) -> None:
    try:
        from tools.notify.send import send
        send(f"A one-off command in the tool server used a whole CPU core for {BUSY_FOR // 60} minutes, so it was "
             f"stopped. The server itself is fine. It was: {cmd[:160]}", title="Stopped a runaway command",
             priority="default", tags=["warning"])
    except Exception:  # noqa: BLE001 - the push is a courtesy; the stop is what matters
        pass


def _on() -> bool:
    try:
        import runtime_settings
        return runtime_settings.get("stray_command_stop") is not False
    except Exception:  # noqa: BLE001 - settings unreadable: keep watching
        return True


def _loop() -> None:
    seen: dict = {}
    while True:
        try:
            if _on():
                for pid, cmd in step(seen, strays(), CHECK_S):
                    _stop(pid)
                    seen.pop(pid, None)
                    _tell(cmd)
        except Exception:  # noqa: BLE001 - the watch must never die
            pass
        time.sleep(CHECK_S)


def start() -> None:
    """The watch, in the background; the server calls it once. Only on Linux (it reads /proc)."""
    global _started
    with _lock:
        if _started or not PROC.is_dir():
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="stray-watch").start()
