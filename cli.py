"""homeshed-mcp: the command a person runs (release v1, 2026-09-29).

    homeshed-mcp [serve]           run the server for one AI app, over stdio (what `uvx homeshed-mcp` does)
    homeshed-mcp serve --http      run it over HTTP instead (Docker, or a shared server); needs MCP_AUTH_TOKEN
    homeshed-mcp init              for HTTP/Docker: write generated secrets into .env (never overwrites a key)
    homeshed-mcp setup             find a local model, show how to connect each AI app, and offer to add it to
                                   Claude Code ([Y/n]; --yes / --no for scripts)
    homeshed-mcp doctor [--json]   what works, what doesn't and the fix for each; exit code 1 if something is broken
    homeshed-mcp panel             the optional web panel on this machine (install with the panel extra)
    homeshed-mcp pro connect|status|disconnect      HomeShed Pro with a key made on the Pro website (never a password)
    homeshed-mcp packs [list]|install|add|check|remove|on|off     rule packs, and their hook in Claude Code

Everything the server keeps lives under DATA_DIR (paths.py): /data in Docker, and on someone's own machine a
per-user folder (Windows %LOCALAPPDATA%\\homeshed-mcp, macOS ~/Library/Application Support/homeshed-mcp, Linux
$XDG_DATA_HOME/homeshed-mcp). The server's modules sit flat next to this file, and the package build puts them all
inside one private package, so this puts its own folder on sys.path before importing them.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "homeshed-mcp"
PROBES = (("Ollama", "http://localhost:11434/v1"), ("LM Studio", "http://localhost:1234/v1"),
          ("llama.cpp", "http://localhost:8080/v1"))
# Where a service's own setting falls back to another (image/generate.py, knowledge/search.py, summarize_file.py).
FALLBACK = {"IMAGE_BASE_URL": ["LOCAL_AI_DECIDE_BASE_URL"], "KNOWLEDGE_BASE_URL": ["LOCAL_AI_DECIDE_BASE_URL"],
            "SUMMARIZE_BASE_URL": ["LOCAL_AI_DECIDE_BASE_URL"], "LOCAL_AI_BASE_URL": ["LOCAL_AI_GATEWAY_URL"]}
HINTS = {  # a copy-paste next step for the common groups; anything else gets "set VAR=..."
    "model server": f"install Ollama from https://ollama.com, then run  uvx {NAME} setup  again",
    "ntfy": "NTFY_BASE_URL=https://ntfy.sh",
    "Docker socket": "see docs/configuration.md#docker (the socket gives root on this machine)",
    "graphify": "pip install graphifyy, then build a graph for your project",
}
LABELS = {  # the manifests' requirement names, as a person would say them
    "a YouTube app and connection": "YouTube", "embedding service": "Docs search", "GPU image service": "Images",
    "summarise service": "File summaries", "Uptime Kuma's database": "Uptime Kuma", "model server": "Local model",
    "memory-core": "Memory", "voice service": "Voice", "Docker socket": "Docker",
    "ntfy": "Notifications", "graphify": "Code graph", "internet access": "Web pages",
}
VAR = re.compile(r"\b([A-Z][A-Z0-9]*_[A-Z0-9_]+)\b")
MARKS = {"ready": "✅", "broken": "❌", "optional": "➖", "off": "🔒"}
PLAIN = {"ready": "[ok]", "broken": "[!!]", "optional": "[--]", "off": "[off]"}


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / NAME


def _prepare() -> Path:
    """Before importing the server's modules: their folder on sys.path, and DATA_DIR set (paths.py reads it once)."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    data = Path(os.environ.setdefault("DATA_DIR", str(default_data_dir())))
    data.mkdir(parents=True, exist_ok=True)
    return data


# --- serve ---------------------------------------------------------------------------------------------------------------
def cmd_serve(args) -> int:
    if args.http:
        # Over HTTP, the settings init wrote (./.env by default) apply, with anything already in the environment winning
        # (the prepper's clean-VM test, 2026-10-01: the README's `serve --http` stopped at "MCP_AUTH_TOKEN must be
        # set"). Over stdio a folder's .env is never read: an AI app starts the server in the user's own project.
        for key, value in _env_values(Path(args.env_file)).items():
            os.environ.setdefault(key, value)
    _prepare()
    import logging
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)  # stdout carries the protocol over stdio
    import server
    if args.http:
        server.serve_http(args.port)
    else:
        server.mcp.run(transport="stdio")
    return 0


# --- init ------------------------------------------------------------------------------------------------------------------
def cmd_init(args) -> int:
    """What HTTP/Docker needs in .env: generated secrets and the host allow-list. A key with a value is never replaced;
    an empty one (as copied from .env.example) is filled in where it stands, and a missing one is added."""
    env = Path(args.env_file)
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    lines = text.splitlines()
    value = {ln.split("=", 1)[0].strip(): ln.split("=", 1)[1].strip() for ln in lines if "=" in ln and not ln.startswith("#")}
    port = os.environ.get("MCP_PORT") or "8765"
    wanted = {  # the server refuses to start over HTTP without the first and the last (server.http_checks)
        "MCP_AUTH_TOKEN": lambda: secrets.token_urlsafe(32),
        "VAULT_KEY": lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),  # a Fernet key
        # This machine only, as docker-compose.yml publishes it; the container's healthcheck uses localhost too.
        "MCP_ALLOWED_HOSTS": lambda: f"localhost:{port},127.0.0.1:{port}",
    }
    new = {k: make() for k, make in wanted.items() if not value.get(k)}
    if not new:
        print(f"{env} already has {', '.join(wanted)}; nothing changed.")
        return 0
    for i, ln in enumerate(lines):  # an empty KEY= line is filled where it stands
        key = ln.split("=", 1)[0].strip() if "=" in ln and not ln.startswith("#") else None
        if key in new and key in value:
            lines[i] = f"{key}={new[key]}"
    lines += [f"{k}={v}" for k, v in new.items() if k not in value]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        env.chmod(0o600)
    print(f"Wrote {', '.join(new)} to {env}.")
    if "MCP_AUTH_TOKEN" in new:
        print(f"Your AI app connects with this token (shown once; it's also in {env}):")
        print(f"    {new['MCP_AUTH_TOKEN']}")
    return 0


# --- setup -----------------------------------------------------------------------------------------------------------------
def _get(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """(answered, body). Any HTTP answer, even an error status, means something is listening."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return True, ""
    except Exception:
        return False, ""


def probe_models() -> list[dict]:
    """Local OpenAI-compatible model servers that answer, with the models they offer."""
    found = []
    for name, base in PROBES:
        ok, body = _get(base + "/models")
        if not ok:
            continue
        try:
            models = [m.get("id") for m in json.loads(body).get("data", []) if m.get("id")]
        except (ValueError, AttributeError):
            models = []
        found.append({"name": name, "base_url": base, "models": models})
    return found


def _claude() -> str | None:
    """Claude Code's command, when it's installed for this user."""
    return shutil.which("claude")


def _ask(question: str, default: bool = True) -> bool:
    """A yes/no question (Yes by default unless default=False). With no one at the keyboard (a script, a pipe) it's
    never asked: No."""
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return False
        answer = input(question).strip().lower()
        return answer in ("y", "yes") or (default and answer == "")
    except (EOFError, OSError):
        return False


def _add_to_claude_code(claude: str, env: list[str]) -> bool:
    """`claude mcp add` for this server, user scope: Claude Code's own command edits its own settings. Setup never
    writes an app's config file itself."""
    cmd = [claude, "mcp", "add", NAME, "-s", "user", *[part for e in env for part in ("-e", e)], "--", "uvx", NAME]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Couldn't run Claude Code ({exc}). Run the line above yourself.")
        return False
    said = " ".join(f"{r.stdout or ''} {r.stderr or ''}".split())
    if r.returncode == 0:
        print(f"Done: Claude Code has {NAME} now. Start a new Claude Code session to use it.")
        return True
    if "already exists" in said.lower():
        print(f"Claude Code already has {NAME}. To set it up again: claude mcp remove {NAME} -s user, then run setup.")
        return True
    print(f"Claude Code didn't add it: {said[:300] or 'it gave no reason'}. Run the line above yourself.")
    return False


def cmd_setup(args) -> int:
    data = _prepare()
    print(f"Data folder: {data}")
    found = probe_models()
    env = []
    if found:
        pick = found[0]
        model = (pick["models"] or [""])[0]
        print(f"Local model: {pick['name']} at {pick['base_url']}" + (f", model {model}" if model else ""))
        env = [f"LOCAL_AI_BASE_URL={pick['base_url']}"] + ([f"LOCAL_AI_MODEL={model}"] if model else [])
    else:
        print("Local model: none found (Ollama :11434, LM Studio :1234, llama.cpp :8080). Optional: install Ollama "
              f"from https://ollama.com, then run  uvx {NAME} setup  again")
    if args.model:
        return 0
    pick_groups(args, model_found=bool(found))
    flags = "".join(f" -e {e}" for e in env)
    print("\nConnect Claude Code (one command):")
    print(f"    claude mcp add {NAME} -s user{flags} -- uvx {NAME}")  # the same name as the README and buttons
    code = 0
    claude = _claude()
    if claude and not args.no and (args.yes or _ask(f"Add {NAME} to Claude Code now? [Y/n] ")):
        code = 0 if _add_to_claude_code(claude, env) else 1
    print("\nOther apps (Cursor, VS Code, Claude Desktop): add this server to their MCP settings:")
    print(json.dumps({NAME: {"command": "uvx", "args": [NAME],
                             "env": dict(e.split("=", 1) for e in env)}}, indent=2))
    print(f"\nThen check it:  uvx {NAME} doctor")
    return code


GROUPS_QUESTION = "Numbers to switch on or off (e.g. 2 5), or Enter to keep these: "


def pick_groups(args, model_found: bool, answer=None) -> set[str]:
    """Which optional tool groups the AI sees (toolgroups.py). Detected ones start ticked; at a keyboard the person can
    switch any on or off by number. With no keyboard, or --yes / --no, nothing is asked and the detected ones stay.
    Saves the hidden groups and returns them."""
    import toolgroups
    answer = answer or input
    on = toolgroups.detected(_is_set, docker_ok=_check("Docker socket")[0] == "ready", model_found=model_found,
                             graphify_ok=bool(shutil.which("graphify")),
                             dev_on=any(t.strip().startswith("dev.") for t in os.environ.get("ENABLE_TOOLS", "").split(",")))
    names = list(toolgroups.GROUPS)
    print("\nTool groups: the AI only sees the groups you use (fewer tools = fewer tokens in every conversation and "
          "fewer wrong picks). The core (memory, reasoning, git, web, files, network, repo and more) always loads.")
    keyboard = not (args.yes or args.no) and bool(sys.stdin) and sys.stdin.isatty()
    while True:
        for i, name in enumerate(names, 1):
            print(f"  {i:>2}. [{'x' if name in on else ' '}] {name:<9} {toolgroups.GROUPS[name][0]}")
        if not keyboard:
            break
        try:
            raw = answer(GROUPS_QUESTION).strip()
        except (EOFError, OSError):
            break
        picks = [int(t) for t in re.split(r"[\s,]+", raw) if t.isdigit() and 1 <= int(t) <= len(names)]
        if not picks:  # Enter, or anything that isn't a number on the list: keep these
            break
        for n in picks:
            on ^= {names[n - 1]}
    hidden = set(names) - on
    path = toolgroups.save(hidden)
    shown = ", ".join(n for n in names if n in on) or "none"
    print(f"Saved in {path}. Optional groups in use: {shown}. Change it any time: uvx {NAME} setup "
          "(the AI app picks it up when it next starts the server).")
    return hidden


# --- doctor ----------------------------------------------------------------------------------------------------------------
def _is_set(var: str) -> bool:
    return any((os.environ.get(v) or "").strip() for v in [var, *FALLBACK.get(var, [])])


def _check(req: str) -> tuple[str, str]:
    """(state, detail) for one requirement string from a manifest's `requires`."""
    low = req.lower()
    if low.startswith("docker socket"):
        sock = os.environ.get("DOCKER_HOST") or (r"\\.\pipe\docker_engine" if os.name == "nt" else "/var/run/docker.sock")
        return ("ready", "Docker reachable") if os.environ.get("DOCKER_HOST") or os.path.exists(sock) \
            else ("optional", "no Docker socket (optional)")
    if low.startswith("graphify"):
        return (("ready", "installed; each project needs its graph first (graphify <folder>)") if shutil.which("graphify")
                else ("optional", "graphify not installed"))
    if low.endswith(" on path"):  # a program the tool runs, e.g. "node on PATH" (dev.node)
        program = req.split()[0]
        return ("ready", f"{program} found") if shutil.which(program) else ("optional", f"{program} not on PATH")
    if "internet access" in low:
        return "ready", "uses the internet"
    if "(vault)" in low:
        return "optional", "set it in your .env"
    names = VAR.findall(req)
    if not names:
        return "optional", "not set up"
    first = names[0]
    if first.endswith("_PATH"):  # a file the server reads (the Uptime Kuma database)
        import paths
        path = os.environ.get(first) or paths.data_path("kuma/kuma.db")
        return ("ready", "found") if Path(path).exists() else ("optional", f"not found ({first})")
    alternatives = [n for n in names if n.endswith("_URL")] or [first]
    live = [n for n in alternatives if _is_set(n)]
    if " or a cloud key" in low and not live and _is_set("LOCAL_AI_GATEWAY_URL"):
        live = ["LOCAL_AI_GATEWAY_URL"]
    if not live and not _is_set(first):
        return "optional", f"{first} not set"
    for n in (n for n in live if n.endswith("_URL")):  # only addresses are probed, never a token
        url = next(os.environ.get(v) for v in [n, *FALLBACK.get(n, [])] if os.environ.get(v))
        if not _get(url)[0]:
            return "broken", f"{n}={url} didn't answer"
    return "ready", f"{live[0]} answers" if live and live[0].endswith("_URL") else "set"


def diagnose() -> dict:
    data = _prepare()
    import manifest
    import toolswitch
    groups: dict[str, dict] = {}
    tools = manifest.load_manifests()
    ready = 0
    for m in tools:
        states = []
        for req in m.get("requires") or []:
            key = re.split(r"\s*[(,;]", req, 1)[0].strip()
            name = LABELS.get(key, key)
            g = groups.setdefault(name, {"name": name, "tools": 0, "requirement": req, "key": key})
            g["tools"] += 1
            if "state" not in g:
                g["state"], g["detail"] = _check(req)
            states.append(g["state"])
        if all(s == "ready" for s in states) and not toolswitch.is_disabled(m["id"]):
            ready += 1
    off = sorted(t for t in toolswitch.DEFAULT_OFF if toolswitch.is_disabled(t) and any(m["id"] == t for m in tools))
    for g in groups.values():
        if g["state"] != "ready":
            var = next(iter(VAR.findall(g["requirement"])), "")
            g["next"] = HINTS.get(g["key"]) or (f"set {var}=..." if var else "see docs/configuration.md")
    memory = memory_state()
    import rule_packs
    hook = rule_packs.hook_state()  # reads Claude Code's settings; never changes them
    packs = {"installed": len(rule_packs.installed()), **hook,
             "state": "ready" if hook["current"] else "broken" if hook["on"] else "optional"}
    broken = [g for g in groups.values() if g["state"] == "broken"] + ([memory] if memory["state"] == "broken" else []) \
        + ([packs] if packs["state"] == "broken" else [])
    core = [m for m in tools if not m.get("requires") and not toolswitch.is_disabled(m["id"])]
    import toolgroups
    hidden = sorted(toolgroups.hidden())
    hidden_tools = sum(1 for m in tools if toolgroups.is_hidden(m["id"].split(".", 1)[0]))
    return {"version": _version(), "transport": "stdio", "python": sys.version.split()[0], "data_dir": str(data),
            "core_ready": len(core), "tools_total": len(tools), "hidden_groups": hidden, "hidden_tools": hidden_tools,
            "tools_ready": ready, "groups": sorted(groups.values(), key=lambda g: g["name"].lower()),
            "memory": memory, "packs": packs, "write_tools_off": off, "broken": len(broken)}


def memory_state() -> dict:
    """Which store the memory tools use: the built-in one (memory_local.py) unless MEMORY_CORE_BASE_URL is set."""
    url = (os.environ.get("MEMORY_CORE_BASE_URL") or "").strip()
    if url:
        if _get(url)[0]:
            return {"state": "ready", "detail": f"memory-core at {url} answers"}
        return {"state": "broken", "detail": f"memory-core at {url} didn't answer", "next": "check MEMORY_CORE_BASE_URL, "
                "or remove it to use the built-in store"}
    import memory_local
    try:
        saved = memory_local.count()
    except Exception as exc:  # noqa: BLE001 - a store that can't open is the thing to report
        return {"state": "broken", "detail": f"built-in store at {memory_local.db_path()} won't open ({exc})",
                "next": "check the folder is writable, or set MEMORY_DB_PATH"}
    return {"state": "ready", "detail": f"built-in, {saved} saved ({memory_local.db_path()})"}


def _version() -> str:
    if str(HERE) not in sys.path:  # app_version sits next to this file (inside the package once installed)
        sys.path.insert(0, str(HERE))
    from app_version import version

    return version()


def _marks() -> dict:
    try:
        "✅❌➖🔒".encode(sys.stdout.encoding or "ascii")
        return MARKS
    except (UnicodeEncodeError, LookupError):
        return PLAIN  # a console that can't show them (a legacy Windows code page, a pipe)


def cmd_doctor(args) -> int:
    report = diagnose()
    if args.json:
        print(json.dumps(report, indent=1))
        return 1 if report["broken"] else 0
    mark = _marks()
    dot = " · " if mark is MARKS else " - "
    print(f"HomeShed doctor{dot}{report['version']}{dot}Python {report['python']}{dot}data in {report['data_dir']}\n")
    print(f"{mark['ready']} Core tools      {report['core_ready']} ready (they need nothing else)")
    mem = report["memory"]
    print(f"{mark[mem['state']]} Memory          {mem['detail']}")
    if mem["state"] != "ready":
        print(f"{'':19}Fix: {mem['next']}")
    for g in report["groups"]:
        print(f"{mark[g['state']]} {g['name'][:16]:<16}{g['tools']} tool(s): {g['detail']}")
        if g["state"] != "ready":
            print(f"{'':19}{'Fix' if g['state'] == 'broken' else 'Add'}: {g['next']}")
    pk = report["packs"]
    print(f"{mark[pk['state']]} {'Rule packs':<16}" + (f"on, {pk['installed']} installed" if pk["current"] else
          f"the hook needs refreshing: {pk['why']}" if pk["on"] else f"off ({pk['installed']} installed)"))
    if not pk["current"]:
        print(f"{'':19}{'Fix' if pk['on'] else 'Add'}: {NAME} packs on   (it shows the change and asks first)")
    if report["write_tools_off"]:
        print(f"{mark['off']} Write tools     off: {', '.join(report['write_tools_off'])}")
        print(f"{'':19}Turn on only what you need: ENABLE_TOOLS=git.commit   (dev.* runs code)")
    if report["hidden_groups"]:
        print(f"{mark['optional']} {'Hidden groups':<16}{report['hidden_tools']} tool(s) the AI doesn't see: "
              f"{', '.join(report['hidden_groups'])}")
        print(f"{'':19}Show one: uvx {NAME} setup   (tick it, then restart your AI app)")
    status = "Nothing is broken." if not report["broken"] else f"{report['broken']} problem(s): fix them above, " \
                                                               f"then run  uvx {NAME} doctor  again."
    print(f"\n{report['tools_ready']} of {report['tools_total']} tools ready. {status}")
    return 1 if report["broken"] else 0


# --- command line ----------------------------------------------------------------------------------------------------------
# --- panel -----------------------------------------------------------------------------------------------------------------
PANEL_DIR = HERE / "panel"  # the optional web panel: in the repo and, with the panel extra, in the installed package


def _env_values(path: Path) -> dict:
    """KEY=value lines of an env file; comments and blank lines skipped."""
    if not path.is_file():
        return {}
    pairs = (ln.split("=", 1) for ln in path.read_text(encoding="utf-8").splitlines()
             if "=" in ln and not ln.lstrip().startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def cmd_panel(args) -> int:
    """The optional web panel, on this machine only (127.0.0.1). It talks to a HomeShed already running over HTTP
    (`serve --http`, or Docker) with the owner token `init` wrote. Its own files live under DATA_DIR/panel."""
    data = _prepare()
    if not (PANEL_DIR / "main.py").is_file():
        print(f'This install has no web panel. Install it with the panel extra:\n    uvx --from "{NAME}[panel]" {NAME} panel')
        return 1
    try:
        import fastapi  # noqa: F401  (the panel extra)
    except ImportError:
        print(f'The web panel needs the panel extra:\n    uvx --from "{NAME}[panel]" {NAME} panel')
        return 1
    token = os.environ.get("MCP_AUTH_TOKEN") or _env_values(Path(args.env_file)).get("MCP_AUTH_TOKEN", "")
    if not token:
        print(f"There's no MCP_AUTH_TOKEN in {args.env_file} or the environment. Run  {NAME} init  first, then start "
              f"HomeShed with  {NAME} serve --http.")
        return 1
    server = args.server.rstrip("/")
    if not _get(server + "/healthz")[0]:
        print(f"HomeShed isn't answering at {server} yet. Start it with  {NAME} serve --http  (or Docker); the panel "
              "starts anyway and shows it as down until it answers.")
    from urllib.parse import urlsplit
    panel_data = data / "panel"
    panel_data.mkdir(parents=True, exist_ok=True)
    os.environ.update(MCP_BASE_URL=server, MCP_AUTH_TOKEN=token, MCP_HOST_HEADER=urlsplit(server).netloc,
                      PANEL_DATA_DIR=str(panel_data))
    if str(PANEL_DIR) not in sys.path:
        sys.path.insert(0, str(PANEL_DIR))
    import importlib

    import uvicorn
    panel = importlib.import_module("main")
    print(f"Web panel: http://127.0.0.1:{args.port}  (this machine only). Its first start prints a sign-in password "
          "below, once; change it in Settings.", flush=True)
    uvicorn.run(panel.app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


# --- pro and packs ---------------------------------------------------------------------------------------------------------
def _remote(args) -> tuple[str | None, str]:
    """A HomeShed server to go through (a Docker or HTTP install: --server or HOMESHED_URL) and its owner token; on
    this machine's own install, (None, "")."""
    server = (getattr(args, "server", None) or os.environ.get("HOMESHED_URL") or "").rstrip("/") or None
    if not server:
        return None, ""
    import rule_packs
    try:  # the owner token, or a Pro key, goes only to an address that's safe to send it to (R&D's security review)
        server = rule_packs.server_url(server)
    except rule_packs.PackError as exc:
        raise SystemExit(str(exc)) from None
    token = os.environ.get("MCP_AUTH_TOKEN") or _env_values(Path(args.env_file)).get("MCP_AUTH_TOKEN", "")
    return server, token


def _say_pro(st: dict) -> None:
    if not st.get("connected"):
        print(st.get("reason") or f"Not connected to HomeShed Pro. Make a key on the Pro website (your account page, "
                                  f"Access keys), then run  {NAME} pro connect")
        return
    name = f' with the key "{st["client_name"]}"' if st.get("client_name") else ""
    print(f"Connected{name}. " + ("Pro is active." if st.get("pro") else st.get("reason") or "Pro isn't active."))


def cmd_pro(args) -> int:
    """HomeShed Pro: paste a key made on the Pro website. It's typed without showing, checked with the site, and kept
    in the vault; no password is ever asked for (anyone can fork HomeShed, so a copy asking for one could be phishing)."""
    _prepare()
    server, token = _remote(args)
    import httpx

    def remote(method: str, path: str, body: dict | None = None) -> dict:
        try:
            r = httpx.request(method, server + path, json=body, timeout=30, headers={"Authorization": f"Bearer {token}"})
            data = r.json()
        except (httpx.HTTPError, ValueError):
            raise SystemExit(f"HomeShed isn't answering at {server}.") from None
        if r.status_code != 200:
            raise SystemExit(data.get("error") or f"HomeShed answered HTTP {r.status_code}.")
        return data

    import mavis_pro
    try:
        if args.action == "connect":
            import getpass
            key = sys.stdin.readline().strip() if args.key_stdin else getpass.getpass(
                "Paste your Pro key (it won't show as you paste): ").strip()
            if server:
                st = remote("POST", "/pro/connect", {"key": key})
            else:
                import vault
                if not vault.ensure_local_key():
                    print("This install's vault can't be switched on (its key file is unreadable). See docs/configuration.md.")
                    return 1
                st = mavis_pro.connect_key(key)
        elif args.action == "disconnect":
            out = remote("DELETE", "/pro") if server else mavis_pro.disconnect()
            print("Disconnected." + (f" {out['note']}" if out.get("note") else ""))
            if not server:
                import vault
                if vault.forget_local_key_if_unused():  # nothing else needs this machine's vault key now
                    print("This machine's vault key is deleted too: the vault was empty.")
            return 0
        else:
            st = remote("GET", "/pro?fresh=true") if server else mavis_pro.status(fresh=True)
    except mavis_pro.MavisProError as exc:
        print(exc)
        return 1
    _say_pro(st)
    return 0 if st.get("connected") else 1


def cmd_packs(args) -> int:
    """Rule packs: list what's installed and what Pro offers, install a Pro pack, add your own, check a file, remove one,
    and switch the hook in Claude Code on or off (that last one changes Claude Code's settings, so it asks first)."""
    _prepare()
    import rule_packs as rp
    server, token = _remote(args)
    action = args.action or "list"
    try:
        if action == "list":
            return _packs_list(rp, server, token)
        if action == "check":
            problems = rp.check(args.name)
            print("Good: this pack would install." if not problems else
                  "Not yet:\n" + "\n".join(f"  - {p}" for p in problems))
            return 1 if problems else 0
        if action == "remove":
            rp.remove(args.name)
            print(f"Removed {args.name}. Claude stops following it from its next action.")
            return 0
        if action == "on":
            return _packs_on(rp, args.yes)
        if action == "off":
            res = rp.turn_off()
            print({"off": f"The hook is off: Claude Code no longer runs rule packs ({res['path']}).",
                   "already off": "The hook was already off.",
                   "changed meanwhile": "Claude Code's settings changed while this ran, so nothing was written. Run it "
                                        "again.",
                   "unreadable": f"Your Claude Code settings ({res['path']}) couldn't be read, so nothing was changed. "
                                 f"Remove the hook entries with {rp.MARKER} in their command yourself (under "
                                 f"{', '.join(rp.EVENTS)})."}[res["reason"]])
            return 0 if res["reason"] in ("off", "already off") else 1
        if action == "install" and not server and not _pro_connected():
            print(f"HomeShed Pro isn't connected on this machine yet. Connect first:  {NAME} pro connect")
            return 1
        confirm = lambda notes: _confirm_notes(notes, args.yes)  # noqa: E731
        out = rp.install(args.name, server, token, confirm_notes=confirm) if action == "install" else \
            rp.add(args.name, confirm_notes=confirm)
    except rp.PackError as exc:
        print(exc)
        return 1
    p = out["installed"]
    print(f"Installed {p['title']} {p['version']}.")
    if out["notes"] and not out["notes_on"]:
        print("Its session note stays off: Claude isn't told it. Add the pack again to read and accept it.")
    print("Claude follows it from its next action." if out["hook_on"] else
          f"Its checks start once the hook is on in Claude Code:  {NAME} packs on")
    return 0


def _confirm_notes(notes: list[str], yes: bool) -> bool:
    """A pack from a file, or fetched through a HomeShed server, that adds a session note: show exactly what Claude
    would be told at the start of every session, and ask (R&D's reviews). Without a keyboard to answer, the note stays
    off; the rest of the pack works."""
    print("This pack tells Claude the following at the start of every session:")
    for note in notes:
        print(f"  \"{note}\"")
    return yes or _ask("Let Claude read this each session? [y/N] ", default=False)


def _pro_connected() -> bool:
    """A Pro key is saved on this machine (looked up locally: nothing is asked of the website)."""
    import mavis_pro
    import vault
    try:
        return bool(vault.secret(mavis_pro.TOKEN))
    except Exception:  # noqa: BLE001 - an unreadable vault means not connected, and the message says how to fix it
        return False


def _packs_list(rp, server, token) -> int:
    packs = rp.installed()
    print(f"Installed rule packs ({len(packs)}):" if packs else "No rule packs installed yet.")
    for p in packs:
        print(f"  {p['slug']:<20} {p['title']} {p['version']} ({p['source']})")
        for note in p["notes"]:  # what Claude is told every session, in full (R&D's review)
            print(f"  {'':<20} Tells Claude each session{'' if p['notes_on'] else ' (off: not confirmed)'}: \"{note}\"")
    state = rp.hook_state()
    print("The hook is on in Claude Code." if state["current"] else
          f"The hook is off: turn it on with  {NAME} packs on" if not state["on"] else
          f"The hook needs refreshing ({state['why']}):  {NAME} packs on")
    offer = rp.offered(server, token)
    if not offer.get("configured"):
        print(f"HomeShed Pro's curated packs: connect first with  {NAME} pro connect" + (
            f" ({offer['error']})" if offer.get("error") else ""))
    elif offer.get("error"):
        print(offer["error"])
    else:
        have = {p["slug"] for p in packs}
        rows = [a for a in offer.get("available") or [] if a.get("slug") not in have]
        print("HomeShed Pro offers:" if rows else "You have every pack HomeShed Pro offers.")
        for a in rows:
            print(f"  {a['slug']:<20} {a.get('title') or ''}: {a.get('description') or ''}")
        if rows:
            print(f"Install one with  {NAME} packs install <name>")
    return 0


def _packs_on(rp, yes: bool) -> int:
    def confirm(entries, path) -> bool:
        print(f"This adds HomeShed's rule-pack hook to Claude Code's settings ({path}). It's one small program, run "
              "before each command, file write or message (PreToolUse), and, for the built-in checks, when a prompt "
              "arrives, when a session starts and when a turn ends:")
        print(json.dumps({"hooks": {event: [entry] for event, entry in entries.items()}}, indent=2))
        print(f"A copy of that file is kept first ({path.name}.homeshed-backup). Undo it any time:  {NAME} packs off")
        return yes or _ask("Add it? [y/N] ", default=False)

    res = rp.turn_on(confirm=confirm)
    if res["reason"] == "unreadable":
        print(f"Your Claude Code settings ({res['path']}) couldn't be read as JSON, so nothing was changed. Add these "
              "under \"hooks\" yourself:")
        print(json.dumps({event: [entry] for event, entry in res["entries"].items()}, indent=2))
        return 1
    print({"on": "The hook is on: Claude follows your rule packs from its next action.",
           "already on": "The hook was already on, and it's up to date.",
           "declined": "Nothing was changed.",
           "changed meanwhile": "Claude Code's settings changed while this ran, so nothing was written. Run it again."
           }[res["reason"]])
    return 0 if res["reason"] != "changed meanwhile" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog=NAME, description="Every tool your AI needs, in one install.")
    sub = p.add_subparsers(dest="command")
    s = sub.add_parser("serve", help="run the server (stdio by default)")
    s.add_argument("--http", action="store_true", help="serve over HTTP instead (Docker, a shared server)")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--env-file", default=".env", help="with --http: the settings init wrote (the environment wins)")
    i = sub.add_parser("init", help="write generated secrets into .env for HTTP/Docker")
    i.add_argument("--env-file", default=".env")
    st = sub.add_parser("setup", help="find a local model and show how to connect an AI app")
    st.add_argument("--model", action="store_true", help="only look for a local model")
    answer = st.add_mutually_exclusive_group()
    answer.add_argument("--yes", action="store_true", help="add it to Claude Code without asking")
    answer.add_argument("--no", action="store_true", help="only print how to connect; never ask or change anything")
    d = sub.add_parser("doctor", help="what works, what doesn't, and the fix")
    d.add_argument("--json", action="store_true")
    pn = sub.add_parser("panel", help="run the optional web panel on this machine (needs the panel extra)")
    pn.add_argument("--port", type=int, default=9090)
    pn.add_argument("--server", default=f"http://127.0.0.1:{os.environ.get('MCP_PORT') or '8765'}",
                    help="where HomeShed answers over HTTP")
    pn.add_argument("--env-file", default=".env", help="where init wrote MCP_AUTH_TOKEN")
    remote = argparse.ArgumentParser(add_help=False)  # for a Docker or HTTP install: go through its server
    remote.add_argument("--server", help="a HomeShed running over HTTP (Docker); default: this machine's own install")
    remote.add_argument("--env-file", default=".env", help="with --server: where init wrote MCP_AUTH_TOKEN")
    pr = sub.add_parser("pro", parents=[remote], help="HomeShed Pro: connect with a key from the Pro website")
    pr.add_argument("action", nargs="?", default="status", choices=["connect", "status", "disconnect"])
    pr.add_argument("--key-stdin", action="store_true", help="connect: read the key from stdin (for scripts)")
    pk = sub.add_parser("packs", parents=[remote], help="rule packs, and their hook in Claude Code")
    pk.add_argument("action", nargs="?", default="list",
                    choices=["list", "install", "add", "check", "remove", "on", "off"])
    pk.add_argument("name", nargs="?", help="install/remove: a pack's name; add/check: a pack file")
    pk.add_argument("--yes", action="store_true",
                    help="on: change Claude Code's settings without asking; add: accept the pack's session notes")
    args = p.parse_args(argv)
    if not args.command:
        args = p.parse_args(["serve", *(argv or [])])
    if args.command == "packs" and args.action in ("install", "add", "check", "remove") and not args.name:
        p.error(f"packs {args.action} needs a {'pack name' if args.action in ('install', 'remove') else 'pack file'}")
    return {"serve": cmd_serve, "init": cmd_init, "setup": cmd_setup, "doctor": cmd_doctor,
            "panel": cmd_panel, "pro": cmd_pro, "packs": cmd_packs}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
