"""Capability registry: auto-discovers @tool-decorated functions under tools/.

Adding a capability = one new tools/<category>/<name>.py file. Nothing else to wire up.
"""
from __future__ import annotations

import functools
import importlib
import logging
import pkgutil
import time
import traceback
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mcp.server.mcpserver.exceptions import ToolError

import clients
import toolswitch
import usage

_logger = logging.getLogger(__name__)

_REGISTRY: list["Capability"] = []

# Real invocation log — every actual call through the registered (MCP-served) capability, not
# direct test imports (tests call the original unwrapped function, see wrap() below). Bounded
# ring buffer, in-memory only, resets on container restart — this is a live activity feed, not
# an audit trail; durable history belongs in memory.* facts, not here.
_ACTIVITY_LOG: deque[dict] = deque(maxlen=200)
# A read the Control Panel makes to draw itself (server.py sets it for the owner's calls marked X-Homelab-Quiet): served,
# but not logged as activity or usage. The owner, 2026-10-02: 192 of the last 200 recent calls were the panel's own
# repo.progress refresh, every 20 s, counted as reads. Only read tools: anything that changes something is always logged.
quiet_call: ContextVar[bool] = ContextVar("quiet_call", default=False)


def get_activity_log() -> list[dict]:
    """Newest-first recent capability invocations: {id, timestamp, duration_ms, ok, refused?}."""
    return list(reversed(_ACTIVITY_LOG))


def log_refusal(capability_id: str, client: str | None, reason: str, conn: str | None = None) -> None:
    """A call refused before it ran (not granted to the client, or over its rate limit)."""
    _ACTIVITY_LOG.append({"id": capability_id, "timestamp": time.time(), "duration_ms": 0.0, "ok": False,
                          "refused": True, "client": client, "conn": conn, "reason": reason[:120]})
    usage.record_refusal(client)


def tool_meta(tool_id: str) -> tuple[str, str]:
    """(risk, category) of a tool, from its (cached) manifest, for client grants (server.py and nested calls here)."""
    from manifest import load_manifests  # manifest.py never imports tools/, so this can't loop

    for m in load_manifests():
        if m["id"] == tool_id:
            return m.get("risk", "read"), m["category"]
    return "write", tool_id.split(".", 1)[0]  # unknown: treat as the riskier kind


def get_capability(capability_id: str) -> "Capability | None":
    """Look up a registered capability by its full id (e.g. "docker.container.restart") --
    for workflow.run, which needs the real callable, not just manifest metadata (that's
    manifest.py's job, and it deliberately never imports tools/ — see its own docstring).
    Returns None rather than raising so callers control their own not-found error message."""
    return next((c for c in _REGISTRY if f"{c.category}.{c.name}" == capability_id), None)


@dataclass
class Capability:
    name: str
    category: str
    doc: str
    func: Callable


def tool(*, name: str, category: str, doc: str):
    """Decorator: registers func as a capability. doc is the path to its Layer-1 .md file."""

    def wrap(func: Callable) -> Callable:
        capability_id = f"{category}.{name}"

        @functools.wraps(func)
        def logged(*args, **kwargs):
            # Switched off by the owner: refuse clearly, log it as a refusal
            # (not a tool error, so it doesn't skew the tool's success rate).
            client, conn = clients.current_client.get(), clients.current_connection.get()
            reason = toolswitch.why_disabled(capability_id)
            if reason:
                _ACTIVITY_LOG.append({"id": capability_id, "timestamp": time.time(), "duration_ms": 0.0,
                                      "ok": False, "refused": True, "client": client, "conn": conn})
                usage.record_refusal(client)
                raise ToolError(reason)
            # A client's grants cover everything its call runs, not just the tool it named: workflow.run's steps
            # used to run any enabled tool (R&D F2, 2026-09-30). server.py checked the named tool; anything nested is
            # checked here, and counts toward the client's calls-per-minute limit.
            if client is not None and clients.current_tool.get() != capability_id:
                ok, why = clients.check_call(client, capability_id, *tool_meta(capability_id))
                if not ok:
                    log_refusal(capability_id, client, why, conn)
                    raise ToolError(why)
            if quiet_call.get() and client is None and tool_meta(capability_id)[0] == "read":
                return func(*args, **kwargs)  # the panel drawing itself: not activity (quiet_call above)
            start = time.monotonic()
            try:
                result = func(*args, **kwargs)
                _ACTIVITY_LOG.append(
                    {
                        "id": capability_id,
                        "timestamp": time.time(),
                        "duration_ms": round((time.monotonic() - start) * 1000, 1),
                        "ok": True,
                        "client": client,
                        "conn": conn,
                    }
                )
                usage.record(capability_id, kwargs, result, ok=True, client=client)  # sizes only; never raises
                return result
            except Exception as exc:
                _ACTIVITY_LOG.append(
                    {
                        "id": capability_id,
                        "timestamp": time.time(),
                        "duration_ms": round((time.monotonic() - start) * 1000, 1),
                        "ok": False,
                        "client": client,
                        "conn": conn,
                    }
                )
                usage.record(capability_id, kwargs, None, ok=False, client=client)
                # Real bug found live 2026-09-23: the MCP SDK's own Tool.run() treats any bare
                # exception as "a crash" and deliberately strips its message before it reaches
                # the client — every one of this project's capabilities has always documented
                # its own "Raises: ValueError if..." (an intentional, anticipated failure, per
                # the SDK's own ToolError docstring), but the client only ever saw the generic
                # "Error executing tool <name>", never the actual reason, since none of them
                # raised the SDK's ToolError specifically. Fixed here once, for every capability,
                # instead of rewriting 28 individual tools/ files to raise ToolError themselves.
                # The full traceback is still logged server-side (ERROR level) before
                # re-raising, so nothing is lost for a genuine, undocumented bug either.
                _logger.error("capability %s raised:\n%s", capability_id, traceback.format_exc())
                raise ToolError(str(exc)) from exc

        # Registry gets the LOGGED wrapper (this is what server.py actually serves over MCP) —
        # the decorator still returns the original func so direct imports (every test file does
        # `from tools.x.y import y` and calls it straight) stay unaffected and unlogged. Only
        # real invocations through the served capability show up in the activity log.
        _REGISTRY.append(Capability(name=name, category=category, doc=doc, func=logged))
        return func

    return wrap


def all_capabilities() -> list[Capability]:
    """The registered capabilities (after discover()), for status views."""
    return list(_REGISTRY)


def discover(tools_pkg: str = "tools") -> list[Capability]:
    """Import every module under tools/ so their @tool decorators run, then return the registry."""
    root = Path(__file__).parent / tools_pkg
    package = importlib.import_module(tools_pkg)
    for _, module_name, is_pkg in pkgutil.walk_packages(package.__path__, prefix=f"{tools_pkg}."):
        if not is_pkg:
            importlib.import_module(module_name)
    return list(_REGISTRY)
