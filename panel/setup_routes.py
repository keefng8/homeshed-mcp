"""Settings > System > Set up (the owner, 2026-10-30: "ensure the shared memory setup and helper pc etc can be setup for
the end-users", "a setup button", "include a ai prompt if needed"). One card checks what this install has: memory, AI
apps on this PC, another PC sharing the memory, a local model and the helper PC. Each one is ready, missing or optional.
Where the panel can't do a step itself, it gives a prompt to paste into the person's own AI app, with this PC's details
filled in. A prompt never holds a token or password: it says which file holds one, and the person copies it.

Linking another PC's folder to a project here is the one change the card makes itself (POST /projects on the tool
server, a dir:<folder> link on the built-in store). Owner only: none of these routes are in VIEWER_ROUTES.
"""
from __future__ import annotations

import re
import socket
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
UVX_PANEL = 'uvx --from "homeshed-mcp[panel]" homeshed-mcp panel'


def _settings_file(m) -> str:
    """Where this install's settings live, for a prompt: the data folder's .env (the panel's data is DATA_DIR/panel)."""
    own = m.PANEL_DATA_DIR.parent / ".env"
    return str(own) if own.is_file() else "the .env file that `homeshed-mcp init` wrote"


async def _answers(client: httpx.AsyncClient, url: str, headers: dict | None = None) -> bool:
    try:
        return (await client.get(url, headers=headers or {})).status_code < 500
    except httpx.RequestError:
        return False


def _memory_prompt() -> str:
    return ("Please connect this AI app to HomeShed's memory on this PC. HomeShed is the MCP server `homeshed-mcp`, "
            "started with the command `uvx homeshed-mcp` (stdio). Add it to this app's MCP settings if it isn't there "
            "yet (for Claude Code: `claude mcp add homeshed-mcp -s user -- uvx homeshed-mcp`), and tell me when to "
            "restart the app. Then check it works: call memory.recall_facts and tell me what it says. From now on, when "
            "you save or recall memory for a project, pass project_dir set to that project's folder.")


def _this_pc_prompt(settings: str, host: str, port: str) -> str:
    return (f"Please let my other PC reach HomeShed on this PC, on my home network only. HomeShed's settings are in "
            f"{settings}. In that file: set MCP_BIND=0.0.0.0, and add {host}:{port} to MCP_ALLOWED_HOSTS (keep what's "
            f"there, comma-separated). Then restart HomeShed: `{UVX_PANEL} --stop`, then `{UVX_PANEL} --start`. "
            f"Port {port} must be open for Private networks only in Windows Firewall: show me the exact command and "
            f"ask me before you run it. Never show me MCP_AUTH_TOKEN or any other value from that file. Finally check "
            f"http://{host}:{port}/healthz answers.")


def _other_pc_prompt(host: str, port: str) -> str:
    return (f"Please connect this AI app to HomeShed running on my other PC, at http://{host}:{port}/mcp (HTTP "
            f"transport). It needs the owner token: I'll copy MCP_AUTH_TOKEN from that PC's HomeShed settings file "
            f"myself and save it on this PC as a user environment variable named HOMESHED_TOKEN, so tell me how to do "
            f"that and never ask me to paste it in chat. Configure the server with the header Authorization: Bearer "
            f"${{HOMESHED_TOKEN}} (for Claude Code, in .mcp.json or with `claude mcp add --transport http`). Then check "
            f"it works with memory.recall_facts. Finally tell me this project's full folder path on this PC, so I can "
            f"link it to the same project on the other PC (Settings > System > Set up there).")


def _model_prompt(settings: str) -> str:
    return ("Please set up a free local AI model for HomeShed on this PC. Install Ollama from https://ollama.com (ask "
            "me before installing anything), then pull a small model such as qwen3:8b. Run `uvx homeshed-mcp setup "
            "--model` to check HomeShed finds it, and add the LOCAL_AI_BASE_URL and LOCAL_AI_MODEL lines it prints to "
            f"{settings}. Then restart HomeShed: `{UVX_PANEL} --stop`, then `{UVX_PANEL} --start`, and check "
            "local_ai.ask answers.")


@router.get("/api/setup")
async def setup_checks(request: Request):
    import main as m
    settings = _settings_file(m)
    port = str(urlsplit(m.MCP_BASE_URL).port or 8765)
    host = socket.gethostname()
    async with httpx.AsyncClient(timeout=3) as client:
        server_up = await _answers(client, f"{m.MCP_BASE_URL}/healthz", m._MCP_HEADERS)
        core_up = bool(m.MEMORY_CORE_BASE_URL) and await _answers(client, f"{m.MEMORY_CORE_BASE_URL}/health")
        helper_up = bool(m.GPU_SERVICE_URL) and await _answers(client, f"{m.GPU_SERVICE_URL}/health")
        try:  # the models local_ai.ask uses (names only): a local one that's set up and not benched
            r = await client.get(f"{m.MCP_BASE_URL}/local-ai/backends", headers=m._MCP_HEADERS)
            models_up = [b for b in (r.json().get("backends") or []) if b.get("configured") and not b.get("cloud")
                         and not b.get("benched")] if r.status_code == 200 else []
        except (httpx.RequestError, ValueError, AttributeError):
            models_up = []
        try:
            r = await client.get(f"{m.MCP_BASE_URL}/projects", headers=m._MCP_HEADERS)
            links = (r.json().get("projects") or []) if r.status_code == 200 else []
        except (httpx.RequestError, ValueError, AttributeError):
            links = []
    store = "memory-core" if m.MEMORY_CORE_BASE_URL else "the built-in store"
    memory_ok = core_up if m.MEMORY_CORE_BASE_URL else server_up
    steps = [
        {"id": "memory", "title": "Memory on this PC",
         "status": "ready" if memory_ok else "broken",
         "detail": (f"Your AI apps share one memory ({store}); each project folder keeps its own, and the shared "
                    "facts reach them all." if memory_ok else f"HomeShed's memory ({store}) isn't answering. Restart "
                    f"it: {UVX_PANEL} --start")},
        {"id": "apps", "title": "Your AI apps on this PC", "status": "optional",
         "detail": "Each AI app (Claude Code, Claude Desktop, Cursor) uses this memory once HomeShed is added to it.",
         "prompt": _memory_prompt()},
        {"id": "other-pc", "title": "Another PC sharing this memory",
         "status": "ready" if links else "optional",
         "detail": (f"{len(links)} folder(s) linked to a project. " if links else "")
         + "First let the other PC reach this one (prompt 1, here), then connect its AI app (prompt 2, there), then "
           "link its project folder below.",
         "prompts": [{"label": "1. On this PC", "text": _this_pc_prompt(settings, host, port)},
                     {"label": "2. On the other PC", "text": _other_pc_prompt(host, port)}],
         "link": True},
        {"id": "model", "title": "A local AI model (free)",
         "status": "ready" if models_up else "optional",
         "detail": (f"Set up: {', '.join(b['name'] for b in models_up)}." if models_up else
                    "Drafts and summaries can run on this PC for free, with nothing sent out."),
         **({} if models_up else {"prompt": _model_prompt(settings)})},
        {"id": "helper", "title": "Helper PC (GPU)",
         "status": "ready" if helper_up else "broken" if m.GPU_SERVICE_URL else "unavailable",
         "detail": ("Images, document search and file summaries run on it." if helper_up else
                    "The helper PC is set (GPU_SERVICE_URL) but isn't answering." if m.GPU_SERVICE_URL else
                    "A helper service for a second PC with a graphics card isn't part of this release yet.")},
    ]
    return {"steps": steps}


@router.post("/api/setup/link")
async def setup_link(request: Request):
    """{folder: another PC's project folder, project: the picker's id (a folder here, or memory-core's agt-...),
    name}: that folder's AI sessions use this project's memory."""
    import main as m
    try:
        body = await request.json()
        folder, project = str(body.get("folder", "")).strip(), str(body.get("project", "")).strip()
        name = re.sub(r"[^a-z0-9-]+", "-", str(body.get("name") or project).lower()).strip("-")[:40] or "project"
    except (ValueError, AttributeError):
        return JSONResponse({"error": "Send the other PC's folder and the project."}, status_code=400)
    if not folder or not project:
        return JSONResponse({"error": "Choose a project and give the other PC's folder."}, status_code=400)
    agent = project if project.startswith("agt-") else "dir:" + project
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{m.MCP_BASE_URL}/projects", headers=m._MCP_HEADERS,
                                  json={"path": folder, "name": name, "agent": agent})
        data = r.json()
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "HomeShed didn't answer."}, status_code=502)
    if r.status_code != 200:
        return JSONResponse({"error": data.get("error") or "HomeShed refused the link."}, status_code=400)
    return {"linked": data}
