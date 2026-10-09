"""memory.capture. See ../../capabilities/memory/capture.md."""
from __future__ import annotations

import os
import re
import secrets
import threading

import httpx

from registry import tool

MAX_CONTENT_CHARS = 16_000

# The dedicated high-priority facts tier (memory.remember_fact/memory.recall_facts) — kept small
# and current, distinct from topic-specific session_ids used for lower-value implementation detail
# (see .claude/local-ai-operating-rules.md-style convention established 2026-09-21).
FACTS_SESSION_ID = "facts"

# Categories split the facts tier into separate per-topic "logs" so a caller can recall just the slice it
# needs instead of every fact ever recorded. "general" is the original, pre-category bucket —
# kept as the literal bare "facts" session_id so existing facts stay reachable unchanged; every
# other category gets its own "facts:<category>" session_id, a genuinely separate log.
DEFAULT_FACT_CATEGORY = "general"
CATEGORY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class MemoryError(RuntimeError):
    """Any memory-core failure — unreachable, misconfigured, or rejected the request."""


def facts_session_id(category: str) -> str:
    if not CATEGORY_PATTERN.match(category):
        raise MemoryError(
            f"category must be 1-40 chars, lowercase letters/digits/hyphens only, got {category!r}"
        )
    return FACTS_SESSION_ID if category == DEFAULT_FACT_CATEGORY else f"{FACTS_SESSION_ID}:{category}"


SCOPES = ("project", "global")


LOCAL_SHARED = "shared"  # the built-in store's platform-wide agent (scope "global", and anyone without a project)
APP_AGENT = "app:"       # the built-in store: an app's own memory is the agent "app:<name>"
_agent_lock = threading.Lock()  # one memory-core agent per app, even when its first calls arrive together


def _config(scope: str = "project", project_dir: str = "") -> dict:
    """Memory settings for this call. scope "project": an app always uses its own memory (the agent the owner set,
    else one made for it on first use: never the shared one), and the owner's sessions use the agent registered for
    the folder they work in (project_dir, or the X-Homelab-Project header: projects.py); "global": the shared
    platform agent, for the owner and for any app whose shared reads the owner hasn't switched off (the owner's decision,
    2026-10-01). An app's writes to "global" never get here: they wait for the owner (capture, memory_pending.py).

    Without MEMORY_CORE_BASE_URL: {"local": True, "agent_id"} for the built-in store (memory_local.py, release v1)."""
    if scope not in SCOPES:
        raise MemoryError(f"scope must be one of {SCOPES}, got {scope!r}")
    import clients
    client = clients.current_client.get()
    if scope == "global" and client is not None and not clients.shared_read_for(client):
        raise MemoryError("this app can't read the shared memory: the owner switched that off for it "
                          "(the web panel, API access); its own memory works as usual (scope \"project\")")
    import vault  # stored credentials first, then .env (vault.py)
    if not vault.secret("MEMORY_CORE_BASE_URL"):
        return {"local": True, "agent_id": _local_agent(scope, project_dir)}
    required = {
        "base_url": "MEMORY_CORE_BASE_URL",
        "bearer": "MEMORY_CORE_BEARER",  # secret-scan: allow (an env var's name, not a value)
        "service_id": "MEMORY_SERVICE_ID",
        "user_key": "MEMORY_USER_KEY",
        "team_id": "MEMORY_TEAM_ID",
        "user_id": "MEMORY_USER_ID",
        "agent_id": "MEMORY_AGENT_ID",
    }
    values = {k: vault.secret(v) for k, v in required.items()}
    missing = [v for k, v in required.items() if not values[k]]
    if missing:
        raise MemoryError(f"memory capability is not configured — missing: {', '.join(missing)}")
    # An app reads and writes its own memory: the agent the owner set, else one made for it now. The owner's sessions
    # (one owner token for all) use the project registered for their folder, else the shared agent.
    import projects
    agent = None
    if scope == "project":
        if client is not None:  # naming a folder mustn't reach another project's memory
            agent = clients.memory_agent_for(client) or _own_core_agent(client, values)
        else:
            agent = projects.agent_for(project_dir or projects.current_dir.get())
    if agent:
        values["agent_id"] = agent
    return values


def _own_core_agent(client: str, cfg: dict) -> str:
    """An app's own memory-core agent, made on its first memory call and kept on the client, as the starter kit's
    bootstrap-new-project.ps1 does for a project. If memory-core won't make one, the call fails: an app never falls
    back to the shared memory."""
    import clients
    with _agent_lock:
        agent = clients.memory_agent_for(client)  # a call that got here first made it
        if agent:
            return agent
        try:
            r = httpx.post(f"{cfg['base_url'].rstrip('/')}/v3/meta/agent/create", timeout=15,
                           headers={"Authorization": f"Bearer {cfg['bearer']}", "x-tdai-service-id": cfg["service_id"],
                                    "x-tdai-user-key": cfg["user_key"]},
                           json={"team_id": cfg["team_id"], "owner_user_id": cfg["user_id"],
                                 "name": f"app-{client}-{secrets.token_hex(3)}"})  # unique: a re-created app starts empty
            data = r.json() if r.status_code == 200 else {"message": f"HTTP {r.status_code}"}
        except (httpx.HTTPError, ValueError):
            data = {"message": "no answer"}
        agent = str((data.get("data") or {}).get("agent_id") or "") if isinstance(data, dict) else ""
        if not isinstance(data, dict) or data.get("code") != 0 or not clients.AGENT_RE.match(agent):
            reason = data.get("message") if isinstance(data, dict) else None
            raise MemoryError(f"couldn't make this app's own memory in memory-core ({reason or 'refused'}); "
                              "an app never uses the shared memory instead")
        try:
            clients.set_memory_agent(client, agent)
        except clients.ClientError as exc:
            raise MemoryError(f"made this app's own memory but couldn't record it: {exc}") from None
    return agent


def _local_agent(scope: str, project_dir: str) -> str:
    """The built-in store's agent: an app's own (the one the owner set, else "app:<name>"); for the owner's sessions,
    the project registered for the folder, else the folder itself, so each project keeps its own memory with no
    setup, else the shared one. The folder: project_dir, the X-Homelab-Project header, or CLAUDE_PROJECT_DIR (set by
    Claude Code for a stdio server). An app never reaches a folder's memory, nor the shared one as its own."""
    if scope != "project":
        return LOCAL_SHARED
    import clients
    import projects
    client = clients.current_client.get()
    if client is not None:
        return clients.memory_agent_for(client) or APP_AGENT + client
    folder = project_dir or projects.current_dir.get() or os.environ.get("CLAUDE_PROJECT_DIR", "")
    agent = projects.agent_for(folder) or (f"dir:{projects.norm(folder)}" if folder.strip() else None)
    return agent or LOCAL_SHARED


def _local(call, *args):
    """Runs a memory_local call; a SQLite failure becomes a MemoryError that says where the store is."""
    import sqlite3

    import memory_local
    try:
        return call(memory_local, *args)
    except (sqlite3.Error, OSError) as exc:
        raise MemoryError(f"the built-in memory store ({memory_local.db_path()}) failed: {exc}") from None


@tool(name="capture", category="memory", doc="memory/capture.md")
def capture(session_id: str, content: str, role: str = "user", project_dir: str = "", scope: str = "project") -> dict:
    """Write one message into persistent cross-session memory (the built-in store, or a self-hosted
    TDAI memory-core when MEMORY_CORE_BASE_URL is set).
    Use this to save a fact, decision, or preference worth recalling in a future session — the
    same store local LLMs write to when routed through the memory proxy.

    Args:
        session_id: stable identifier grouping related messages (e.g. a project or topic slug).
            Messages with the same session_id are recalled together by memory.recall.
        content: the text to remember. Max 16,000 chars.
        role: "user" or "assistant" — who said it. Defaults to "user" (a fact being told to the
            system, as opposed to something the system generated).
        project_dir: the caller's working folder; picks that project's own memory (projects.py).
        scope: "project" (default) writes this project's (or this app's) own memory; "global" writes the shared
            memory. An app's "global" write waits for the owner's approval on the web panel instead.

    Returns:
        {"accepted": bool, "message_id": str | None}; an app's "global" write: {"accepted": false,
        "pending": "<id>", "note"}.

    Raises:
        MemoryError: not configured (see capabilities/memory/capture.md's Configuration section),
            backend unreachable, or the write was rejected.
    """
    if not session_id or not session_id.strip():
        raise MemoryError("session_id must be non-empty")
    if not content or not content.strip():
        raise MemoryError("content must be non-empty")
    if len(content) > MAX_CONTENT_CHARS:
        raise MemoryError(f"content too long ({len(content)} chars, max {MAX_CONTENT_CHARS})")
    if role not in ("user", "assistant"):
        raise MemoryError('role must be "user" or "assistant"')
    if scope not in SCOPES:
        raise MemoryError(f"scope must be one of {SCOPES}, got {scope!r}")
    import clients
    client = clients.current_client.get()
    if scope == "global" and client is not None:  # an app's write to the shared memory waits for the owner
        import memory_pending
        try:
            item = memory_pending.add(client, session_id, role, content)
        except memory_pending.PendingError as exc:
            raise MemoryError(str(exc)) from None
        return {"accepted": False, "message_id": None, "pending": item,
                "note": "Waiting for the owner: a write to the shared memory goes in once the owner approves it on "
                        "the web panel (API access). This app's own memory (scope \"project\") needs no approval."}

    cfg = _config(scope, project_dir=project_dir)
    if cfg.get("local"):
        message_id = _local(lambda m: m.add(cfg["agent_id"], session_id, role, content))
        return {"accepted": True, "message_id": message_id}

    try:
        response = httpx.post(
            f"{cfg['base_url'].rstrip('/')}/v3/conversation/add",
            headers={
                "Authorization": f"Bearer {cfg['bearer']}",
                "x-tdai-service-id": cfg["service_id"],
                "x-tdai-user-key": cfg["user_key"],
            },
            json={
                "team_id": cfg["team_id"],
                "user_id": cfg["user_id"],
                "agent_id": cfg["agent_id"],
                "session_id": session_id,
                "messages": [{"role": role, "content": content}],
            },
            timeout=15,
        )
    except httpx.TimeoutException:
        raise MemoryError("memory-core timed out") from None
    except httpx.RequestError:
        raise MemoryError("memory-core is unavailable") from None

    if response.status_code != 200:
        raise MemoryError(f"memory-core returned HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError:
        raise MemoryError("memory-core returned an unparseable response") from None

    if data.get("code") != 0:
        raise MemoryError(f"memory-core rejected the write: {data.get('message', 'unknown error')}")

    accepted_ids = (data.get("data") or {}).get("accepted_ids") or []
    return {"accepted": bool(accepted_ids), "message_id": accepted_ids[0] if accepted_ids else None}
