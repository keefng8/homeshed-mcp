"""`homeshed-mcp panel --start` (the prepper for the owner, 2026-10-30): the web panel in one command, for a newcomer
on Windows. init if there's no token yet, HomeShed over HTTP and the panel in the background (no terminal to keep
open), then the browser. `--at-sign-in` starts both again when this user signs in to Windows; `panel --stop` stops
what --start started.

The background processes are this same command (`serve --http`, `panel`), so they behave exactly as when run by
hand. Their output goes to DATA_DIR/logs, and their process ids to DATA_DIR/run, which `--stop` reads.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # this user's sign-in list: no admin rights needed
RUN_NAME = "HomeShed"
WAIT_S = 30  # a first start imports every tool module; a slow PC needs most of this


def self_command(cli_module: str, cli_file: str, windowless: bool = False) -> list[str]:
    """This command, run again: as its module when installed (uvx, pip), else as the file (the repo)."""
    exe = sys.executable
    if windowless and os.name == "nt" and Path(exe).with_name("pythonw.exe").is_file():
        exe = str(Path(exe).with_name("pythonw.exe"))  # no console window flashing up at sign-in
    return [exe, "-m", cli_module] if cli_module not in ("__main__", "cli") else [exe, cli_file]


def sign_in_command(cli_module: str, cli_file: str) -> list[str]:
    """The command Windows runs at sign-in. Under uvx this Python lives in uv's cache, which `uv cache clean` or an
    upgrade deletes (the prepper, 2026-10-30), so it goes through uv instead and always runs the current version: uvw,
    uv's own windowless launcher, else uvx."""
    parts = [p.lower() for p in Path(sys.executable).parts]
    if "archive-v0" in parts or ("uv" in parts and "cache" in parts):
        uvw, uvx = shutil.which("uvw"), shutil.which("uvx")
        if uvw:
            return [uvw, "tool", "run", "--from", "homeshed-mcp[panel]", "homeshed-mcp"]
        if uvx:
            return [uvx, "--from", "homeshed-mcp[panel]", "homeshed-mcp"]
    return self_command(cli_module, cli_file, windowless=True)


def _background(cmd: list[str], log: Path, pid_file: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    opts: dict = {"start_new_session": True}
    if os.name == "nt":  # no console of its own, and closing this terminal doesn't stop it
        opts = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    with open(log, "ab") as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **opts)
    pid_file.write_text(str(proc.pid), encoding="utf-8")


def _wait(answers, url: str, seconds: float = WAIT_S) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if answers(url):
            return True
        time.sleep(0.5)
    return False


def start(args, data: Path, env: Path, base: list[str], sign_in_base: list[str], answers, has_token, init) -> int:
    """base / sign_in_base: this command, run again (self_command). answers(url): something listens there.
    has_token(env): init already ran. init(env): run it. The last three come from cli.py."""
    server = args.server.rstrip("/")
    port = str(urlsplit(server).port or 8765)
    if not has_token(env):
        print(f"First start: making the settings HomeShed needs, in {env}")
        os.environ.setdefault("MCP_PORT", port)  # init allows this port's host names
        if init(env) != 0:
            return 1
    logs, run = data / "logs", data / "run"
    if answers(server + "/healthz"):
        print(f"HomeShed is already running at {server}")
    else:
        _background([*base, "serve", "--http", "--port", port, "--env-file", str(env)], logs / "server.log",
                    run / "server.pid")
        if not _wait(answers, server + "/healthz"):
            print(f"HomeShed didn't start within {WAIT_S} s. What it said is in {logs / 'server.log'}")
            return 1
        print(f"HomeShed started at {server} (in the background)")
    panel = f"http://127.0.0.1:{args.port}"
    if not answers(panel + "/"):
        _background([*base, "panel", "--port", str(args.port), "--server", server, "--env-file", str(env)],
                    logs / "panel.log", run / "panel.pid")
        if not _wait(answers, panel + "/"):
            print(f"The panel didn't start within {WAIT_S} s. What it said is in {logs / 'panel.log'}")
            return 1
    print(f"Web panel: {panel}  (this machine only). Both keep running after you close this window; stop them with  "
          f"homeshed-mcp panel --stop")
    first = data / "panel" / "first-run-password.txt"
    if first.is_file():
        print(f"Your first sign-in password is in {first} (open it with Notepad). Change it in Settings > Account "
              "and sharing, and that file is deleted.")
    if args.at_sign_in:
        at_sign_in([*sign_in_base, "panel", "--start", "--no-browser",
                    "--port", str(args.port), "--server", server, "--env-file", str(env)])
    if not args.no_browser:
        webbrowser.open(panel)
    return 0


def at_sign_in(cmd: list[str] | None) -> None:
    """Add (cmd) or remove (None) the sign-in start. Windows only: elsewhere it says what to use instead."""
    if os.name != "nt":
        print("Starting at sign-in is Windows only for now. Elsewhere, add  homeshed-mcp panel --start --no-browser  "
              "to your desktop's startup applications.")
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if cmd:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            print('HomeShed and the panel now start when you sign in to Windows. To undo it: homeshed-mcp panel '
                  '--not-at-sign-in (or switch "HomeShed" off in Settings > Apps > Startup).')
            return
        try:
            winreg.DeleteValue(key, RUN_NAME)
            print("HomeShed no longer starts when you sign in.")
        except FileNotFoundError:
            print("HomeShed wasn't set to start at sign-in; nothing changed.")


def stop(args, data: Path, answers) -> int:
    """Stop what --start started. A process id is only acted on while its address still answers, so an id Windows has
    since given to another program is left alone."""
    server = args.server.rstrip("/")
    stopped = 0
    for name, url in (("panel", f"http://127.0.0.1:{args.port}/"), ("server", server + "/healthz")):
        pid_file = data / "run" / f"{name}.pid"
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if answers(url):
            try:
                os.kill(pid, signal.SIGTERM)  # on Windows: TerminateProcess
                stopped += 1
            except OSError:
                pass
        pid_file.unlink(missing_ok=True)
    print(f"Stopped {stopped} background process(es)." if stopped else "Nothing started by panel --start is running.")
    return 0
