"""The install's three choices (the owner, 2026-10-30: "give the user a choice on install", "both", "and shared memory"),
asked by `homeshed-mcp setup` after the local model and the tool groups:

1. Memory: this PC keeps its own, or it shares the memory of HomeShed on another PC (this PC's AI apps then connect to
   that one over the home network, and nothing runs here).
2. Where HomeShed runs: straight on this PC (the AI app starts it: the default), or in Docker (always on). Docker keeps
   its data in its own volumes, so the AI apps connect to it over HTTP, never through a second copy started by the app.
3. A helper PC: yes or no. The plan a member's own AI builds one to comes with HomeShed Pro (pro_routes._helper_plan).

With no one at the keyboard, or --yes / --no, nothing is asked: this PC, its own memory, no helper. A token is never
printed: it's typed hidden (getpass) or read from the file `init` wrote, and goes only to Claude Code's own command.
"""
from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

NAME = "homeshed-mcp"
REPO = "https://github.com/keefng8/homeshed-mcp"


def _keyboard(args) -> bool:
    return not (args.yes or args.no) and bool(sys.stdin) and sys.stdin.isatty()


def pick(question: str, options: list[str], keyboard: bool, answer=None) -> int:
    """1-based choice; Enter, no keyboard or anything else keeps 1."""
    answer = answer or input
    print(f"\n{question}")
    for i, text in enumerate(options, 1):
        print(f"  {i}. {text}")
    if not keyboard:
        return 1
    try:
        raw = answer(f"Pick 1-{len(options)} (Enter: 1): ").strip()
    except (EOFError, OSError):
        return 1
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= len(options) else 1


def _yes(question: str, keyboard: bool, answer=None, default: bool = False) -> bool:
    answer = answer or input
    if not keyboard:
        return False
    try:
        got = answer(question).strip().lower()
    except (EOFError, OSError):
        return False
    return got in ("y", "yes") or (default and got == "")


def _healthy(base: str, seconds: float = 0) -> bool:
    end = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(base + "/healthz", headers={"User-Agent": NAME}),
                                        timeout=3):
                return True
        except urllib.error.HTTPError:
            return True
        except Exception:  # noqa: BLE001 - not up (yet)
            if time.monotonic() >= end:
                return False
            time.sleep(1)


def choose(args, init, env_values, answer=None, secret=None) -> dict:
    """{"where": "pc"} | {"where": "http", "url", "token", "how": "other-pc" | "docker"}, or {"where": "stop"} when a
    Docker step failed (it says what to do)."""
    answer, secret = answer or input, secret or getpass.getpass
    kb = _keyboard(args)
    memory = pick("Memory: where should your AI's memory live?",
                  ["On this PC (each project keeps its own; your AI apps here share it)",
                   "Shared with HomeShed on another PC on your home network (this PC's AI apps use that one)"], kb, answer)
    if memory == 2:
        host = answer("That PC's address, as Settings > System > Set up shows it there (like my-pc:8765): ").strip()
        url = (host if host.startswith("http") else f"http://{host}").rstrip("/")
        if not _healthy(url):
            print(f"Nothing answers at {url}/healthz. On that PC, use its Set up card's prompt 1 (let other PCs reach "
                  "it), then run setup here again.")
            return {"where": "stop"}
        token = secret("Its owner token (MCP_AUTH_TOKEN in that PC's HomeShed settings file; typing is hidden): ").strip()
        return {"where": "http", "how": "other-pc", "url": url + "/mcp", "token": token} if token else {"where": "stop"}
    place = pick("Where should HomeShed run?",
                 ["On this PC: your AI app starts it when it needs it (simplest)",
                  "In Docker: always on, like a small server (needs Docker Desktop or Docker)"], kb, answer)
    return docker(init, env_values, answer, kb) if place == 2 else {"where": "pc"}


def docker(init, env_values, answer=None, keyboard: bool = True) -> dict:
    """HomeShed from its repo in Docker, each step asked first: get the code, write its settings, start it.
    init(env_file) and env_values(path) are cli.py's (passed in: the installed package names that module differently)."""
    answer = answer or input
    for tool, get in (("docker", "Docker Desktop from https://www.docker.com/products/docker-desktop/"),
                      ("git", "Git from https://git-scm.com/downloads")):
        if not shutil.which(tool):
            print(f"Docker needs {tool}, which isn't installed. Install {get}, then run setup again.")
            return {"where": "stop"}
    if subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode != 0:
        print("Docker is installed but not running. Start Docker Desktop, then run setup again.")
        return {"where": "stop"}
    folder = Path(answer(f"Folder for HomeShed's Docker files (Enter: {Path.home() / 'homeshed'}): ").strip()
                  or Path.home() / "homeshed")
    if not (folder / "docker-compose.yml").is_file():
        if not _yes(f"Download HomeShed's code from {REPO} into {folder}? [Y/n] ", keyboard, answer, default=True):
            return {"where": "stop"}
        if subprocess.run(["git", "clone", "--depth", "1", REPO, str(folder)], timeout=600).returncode != 0:
            print("The download failed (see above). Run setup again when you're online.")
            return {"where": "stop"}
    env = folder / ".env"
    init(env)  # the same init as `homeshed-mcp init`, writing the folder's own .env (Docker reads it)
    if not _yes("Build and start HomeShed and its web panel in Docker now (a few minutes the first time)? [Y/n] ",
                keyboard, answer, default=True):
        return {"where": "stop"}
    if subprocess.run(["docker", "compose", "--profile", "panel", "up", "-d", "--build"], cwd=folder,
                      timeout=1800).returncode != 0:
        print("Docker couldn't start it (see above).")
        return {"where": "stop"}
    if not _healthy("http://localhost:8765", 90):
        print(f"HomeShed didn't answer at http://localhost:8765 yet. Look at  docker compose logs  in {folder}.")
        return {"where": "stop"}
    print(f"HomeShed runs in Docker (from {folder}). Web panel: http://localhost:9090; its first sign-in password: "
          "docker compose logs panel")
    return {"where": "http", "how": "docker", "url": "http://localhost:8765/mcp",
            "token": env_values(env).get("MCP_AUTH_TOKEN", "")}


def add_http_to_claude_code(claude: str, url: str, token: str) -> bool:
    """`claude mcp add --transport http`, user scope, with the token as a header. The token goes only to Claude Code's
    command, never to the screen."""
    cmd = [claude, "mcp", "add", "--transport", "http", "-s", "user", NAME, url,
           "--header", f"Authorization: Bearer {token}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Couldn't run Claude Code ({exc}).")
        return False
    said = " ".join(f"{r.stdout or ''} {r.stderr or ''}".split()).replace(token, "<token>")
    if r.returncode == 0:
        print(f"Done: Claude Code uses HomeShed at {url} now. Start a new Claude Code session to use it.")
        return True
    if "already exists" in said.lower():
        print(f"Claude Code already has {NAME}. To switch it: claude mcp remove {NAME} -s user, then run setup again.")
        return True
    print(f"Claude Code didn't add it: {said[:300] or 'it gave no reason'}")
    return False


def helper(args, data: Path, answer=None) -> None:
    """Question 3: a helper PC. A Pro member gets the build prompt in a file; anyone else hears it comes with Pro."""
    if not _yes("\nDo you want a helper PC (a PC with a graphics card for images, document search and file summaries)? "
                "[y/N] ", _keyboard(args), answer):
        return
    import pro_routes
    got = pro_routes._helper_plan()
    if not got.get("plan"):
        print(got.get("reason") or "The helper PC plan comes with HomeShed Pro.")
        print(f"With Pro: run  uvx {NAME} pro connect  and  uvx {NAME} pro update, then setup again.")
        return
    out = data / "helper-pc-prompt.md"
    out.write_text("Please build a HomeShed helper service on this PC, following the plan below step by step. Ask me "
                   "before installing anything, downloading big files or opening a firewall port, and never show me a "
                   "token.\n\n" + got["plan"], encoding="utf-8")
    print(f"The helper PC's build prompt is in {out}. Copy it to the PC with the graphics card and paste it into the AI "
          "app there (Claude Code, for example). It's also in the web panel: Settings > System > Set up.")
