"""Entrypoint. Discovers all tools, registers them with MCPServer, serves over Streamable HTTP.

Not run on this machine — see README.md for first run on the deploy target.
"""
from __future__ import annotations

import os

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, ImageContent, TextContent
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from manifest import load_manifests
from registry import discover, get_activity_log, quiet_call as registry_quiet

AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN")

# The SDK's DNS-rebinding protection defaults to an empty allowlist, which rejects every
# request's Host/Origin header — including the tunnel hostname in front of this server.
# Must be set explicitly or nothing gets through but a raw IP:port request.
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]


def http_checks() -> None:
    """Over HTTP this server is network-reachable, so it refuses to start without a token and a host allow-list.
    Over stdio it is a child process of one person's own AI app and needs neither (release v1, 2026-09-29): these
    checks moved here from import time so `homeshed-mcp serve` can import this module."""
    if not AUTH_TOKEN:
        raise RuntimeError("MCP_AUTH_TOKEN must be set: over HTTP this server is network-reachable. "
                           "`homeshed-mcp init` writes one into .env.")
    if not ALLOWED_HOSTS:
        raise RuntimeError("MCP_ALLOWED_HOSTS must be set: the host names clients use, e.g. localhost:8765. "
                           "`homeshed-mcp init` writes it into .env.")

import asyncio
import clients
import copy
import frontdoor
import hmac
import inspect
import json
import logging
import projects
import re
import time
import vault
from collections import deque
from urllib.parse import urlsplit

try:  # The owner's private add-on routes (private_routes.py). The public copy leaves it out.
    import private_routes
except ImportError:
    private_routes = None


def _tool_meta(tool_id: str) -> tuple[str, str]:
    """(risk, category) of a tool, from its (cached) manifest. One copy, in registry.py: workflow steps use it too."""
    from registry import tool_meta
    return tool_meta(tool_id)


def _caller(ctx) -> str | None:
    """The client name behind this MCP request, or None for the owner token (full access)."""
    request = getattr(ctx, "request", None)
    auth = request.headers.get("authorization") if request is not None else None
    if auth is None or auth == f"Bearer {AUTH_TOKEN}":
        return None
    name, status = clients.resolve(auth)
    return name if status == "ok" else "<refused>"  # RequestGuard already stopped anything else


def _source(ctx) -> str | None:
    """The address this MCP request came from, for the Clients live panel's connections."""
    return getattr(getattr(getattr(ctx, "request", None), "client", None), "host", None)


def _project_header(ctx) -> str:
    """The folder a session's connection says it works in (X-Homelab-Project), for projects.py; "" if none."""
    request = getattr(ctx, "request", None)
    return (request.headers.get(projects.HEADER) or "") if request is not None else ""


_log = logging.getLogger("homeshed")


def _inputs(tool_name: str) -> list[str]:
    from registry import get_capability
    cap = get_capability(tool_name)
    return list(inspect.signature(cap.func).parameters) if cap else []


def _unknown_inputs(tool_name: str, arguments) -> list[str]:
    """Input names the tool doesn't take. The SDK drops them silently, so a misnamed one ran with its default instead:
    repo.leak_scan given path= (not folder=) scanned the server's own folder and said "clean" (2026-10-14). The owner's
    sessions are told and nothing runs; an app's extra field is only logged, so a client sending one keeps working."""
    from registry import get_capability
    cap = get_capability(tool_name)
    if cap is None or not isinstance(arguments, dict):
        return []
    params = inspect.signature(cap.func).parameters.values()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return []
    return sorted(set(arguments) - {p.name for p in params})


class GuardedMCPServer(MCPServer):
    """MCPServer plus per-client tool grants (clients.py): a client only sees and can only call the
    tools the owner granted it, within its calls-per-minute limit.
    Each connection is also noted (clients.seen) so an admin client can show who is connected."""

    async def _handle_list_tools(self, ctx, params):
        result = await super()._handle_list_tools(ctx, params)
        client = _caller(ctx)
        if client is not None:
            result.tools = [t for t in result.tools if client != "<refused>" and clients.is_allowed(client, t.name, *_tool_meta(t.name))]
            if client != "<refused>" and frontdoor.view(client) == "front_door":  # the rest stay callable by name
                hidden = [t for t in result.tools if t.name not in frontdoor.FRONT_TOOLS]
                result.tools = [t for t in result.tools if t.name in frontdoor.FRONT_TOOLS]
                import usage
                usage.record_front_door(client, hidden_chars=sum(
                    len(t.model_dump_json(by_alias=True, exclude_none=True)) for t in hidden))
        if client != "<refused>":
            clients.seen(client, _source(ctx), call=False)
        return result

    async def _handle_call_tool(self, ctx, params):
        client = _caller(ctx)
        if client is not None:
            ok, reason = (False, "access refused") if client == "<refused>" else clients.check_call(client, params.name, *_tool_meta(params.name))
            if not ok:
                from registry import log_refusal
                log_refusal(params.name, client, reason, clients.connection_tag(client, _source(ctx)))
                return CallToolResult(content=[TextContent(type="text", text=reason)], is_error=True)
        if params.name == "tools.call":
            return await self._front_door_call(ctx, params, client)
        unknown = _unknown_inputs(params.name, getattr(params, "arguments", None))
        if unknown:
            takes = ", ".join(_inputs(params.name)) or "no inputs"
            text = (f"{params.name} has no input named {', '.join(unknown)}: it takes {takes}. Nothing was run; "
                    "call it again with those names.")
            if client is None:
                return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)
            _log.warning("client %s: %s", client, text)  # an app's extra field is logged, never refused here
        conn = clients.seen(client, _source(ctx), call=True)
        token, ctoken = clients.current_client.set(client), clients.current_connection.set(conn)
        # The tool checked just above: anything else a client's call runs (workflow.run's steps) is checked by the
        # registry wrapper against the same grants (R&D F2, 2026-09-30).
        ttoken = clients.current_tool.set(params.name)
        # Only the owner's sessions may name their project: a client token keeps its own memory (projects.py).
        ptoken = projects.current_dir.set(_project_header(ctx) if client is None else "")
        # The Control Panel's own refreshes (X-Homelab-Quiet: 1, owner only): served, not logged (registry.quiet_call)
        request = getattr(ctx, "request", None)
        quiet = client is None and request is not None and request.headers.get("x-homelab-quiet") == "1"
        qtoken = registry_quiet.set(quiet)
        try:
            result = await super()._handle_call_tool(ctx, params)
            if client is not None and frontdoor.image_mode(client) == "content":
                result = _images_as_content(result, params.name)
            return result
        finally:
            registry_quiet.reset(qtoken)
            projects.current_dir.reset(ptoken)
            clients.current_tool.reset(ttoken)
            clients.current_connection.reset(ctoken)
            clients.current_client.reset(token)


    async def _front_door_call(self, ctx, params, client):
        """tools.call: the inner tool goes back through _handle_call_tool as if it had been called directly (grant,
        rate limit, inputs, switches, approvals, spend; usage counts it under its own name), then the reply is trimmed
        or stored. tools.call itself was checked above like any tool. It has no output schema, so a trimmed reply
        can't fail a client's schema check (R&D, 27 Oct)."""
        def refuse(text: str) -> CallToolResult:
            return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)

        args = params.arguments if isinstance(params.arguments, dict) else {}
        extra = sorted(set(args) - {"name", "arguments", "fields", "to_file"})
        if extra:
            return refuse(f"tools.call has no input named {', '.join(extra)}: it takes name, arguments, fields, "
                          "to_file. Nothing was run.")
        name, inner_args = args.get("name"), args.get("arguments") or {}
        fields, to_file = args.get("fields") or [], args.get("to_file") is True
        if not isinstance(name, str) or not name:
            return refuse("tools.call needs name: the tool id, e.g. \"etsy.orders.list\" (tools.find finds one)")
        if name.startswith("tools."):
            return refuse(f"call {name} directly, not through tools.call")
        if not isinstance(inner_args, dict):
            return refuse("arguments is an object of the tool's inputs, e.g. {\"limit\": 5}")
        if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
            return refuse("fields is a list of paths, e.g. [\"items[].title\", \"count\"]")
        inner = copy.copy(params)
        inner.name, inner.arguments = name, inner_args
        result = await self._handle_call_tool(ctx, inner)
        if getattr(result, "is_error", False) or not isinstance(result, CallToolResult):
            return result  # errors and input requests pass through whole
        value = _reply_value(result)
        if value is _NO_VALUE:
            return result
        import usage
        before = usage._size(value)
        try:
            if fields and not _is_queued(value):
                value = frontdoor.trim(value, fields)
            images = []
            if client is not None and frontdoor.image_mode(client) == "content":
                value, images = frontdoor.take_images(value)
            if to_file:
                value = frontdoor.store(value, client, name)
        except frontdoor.FrontDoorError as exc:
            return refuse(str(exc))
        usage.record("tools.call", args, value, ok=True, client=client)  # the wrapper's own count, beside the tool's
        if fields or to_file:
            usage.record_front_door(client, trimmed_chars=max(0, before - usage._size(value)))
        content = [TextContent(type="text", text=json.dumps(value, ensure_ascii=False, default=str))]
        content += [ImageContent(type="image", data=b64, mime_type=mime) for b64, mime in images]
        content += [c for c in result.content if not isinstance(c, TextContent)]  # a tool's own image blocks stay
        return CallToolResult(content=content, is_error=False)


_NO_VALUE = object()


def _reply_value(result: CallToolResult):
    """A tool reply as one value: its structured content, else its JSON text block(s); _NO_VALUE when it's plain text."""
    if isinstance(result.structured_content, dict):
        sc = result.structured_content
        return sc["result"] if set(sc) == {"result"} else sc
    blocks = [c.text for c in result.content if isinstance(c, TextContent)]
    try:
        parsed = [json.loads(b) for b in blocks]
    except ValueError:
        return _NO_VALUE
    if not parsed:
        return _NO_VALUE
    return parsed[0] if len(parsed) == 1 else parsed


def _is_queued(value) -> bool:
    """A reply that queued something for the owner, or answered about an approval: never trimmed (R&D, 27 Oct)."""
    if not isinstance(value, dict):
        return False
    text = " ".join(str(value.get(k, "")) for k in ("status", "note", "approval", "queued")).lower()
    return bool(value.get("queued") or value.get("request_id") or value.get("approval")
                or any(w in text for w in ("waiting for the owner", "queued", "approval")))


def _images_as_content(result, tool_name: str):
    """A direct call's reply with its base64 pictures as MCP image blocks, for a client whose images are "content".
    Only for tools without an output schema: their structured content may be reshaped without failing validation."""
    if not isinstance(result, CallToolResult) or result.is_error:
        return result
    info = mcp._tool_manager.get_tool(tool_name)
    if info is None or info.output_schema:
        return result
    value = _reply_value(result)
    if value is _NO_VALUE:
        return result
    value, images = frontdoor.take_images(value)
    if not images:
        return result
    text = json.dumps(value, ensure_ascii=False, default=str)
    return CallToolResult(content=[TextContent(type="text", text=text)]
                          + [ImageContent(type="image", data=b64, mime_type=mime) for b64, mime in images],
                          structured_content=value if isinstance(value, dict) else None, is_error=False)


def _version() -> str:
    from app_version import version  # pyproject.toml when running from source (Docker), else the installed package

    return version()


# What an app sees when it connects (serverInfo). The installed package's own name and version; it said
# "homelab-capabilities" with no version until the 2026-09-30 wheel smoke test. Apps name the server themselves.
def _instructions() -> str:
    """The server's MCP instructions: what HomeShed offers and how to pick a tool. Clients defer 145 tool schemas
    behind tool search (measured 2026-10-22: ~16k tokens a turn in full, ~1.7k deferred), so a few lines naming the
    groups help it find the right one. Built from this install's own tools, so it never names one it lacks."""
    try:
        import registry
        groups = sorted({c.category for c in registry.discover()})
    except Exception:  # noqa: BLE001 - instructions are a hint; the server must start without them
        groups = []
    return ("HomeShed: tools for this machine and its services, grouped by prefix"
            + (f" ({', '.join(groups)})" if groups else "") + ". Not sure which tool fits? Call registry.find "
            "with the task in plain words. If a local model is set up, local_ai.* handles drafts and summaries for "
            "free. Use memory.* to recall and save facts across sessions, and bugs.find before debugging. Tools that change something "
            "real may queue for the owner's approval instead of acting at once; the reply says so.")


mcp = GuardedMCPServer(name="homeshed-mcp", title="HomeShed", version=_version(),
                       description="Every tool your AI needs, in one install", instructions=_instructions())

def _description(capability) -> str:
    """The capability doc is the real description source, kept short here and pointing back at the .md for detail.
    A tool with a project_dir argument says what to pass, since callers only see the argument's name otherwise."""
    text = f"See capabilities/{capability.doc} for full docs."
    if "project_dir" in inspect.signature(capability.func).parameters:
        text += f" project_dir: {projects.DIR_HELP}"
    return text


import toolgroups  # noqa: E402

for capability in discover():
    if toolgroups.is_hidden(capability.category):  # an optional group this install said it doesn't use (setup)
        continue
    # add_tool wants (fn, name, description).
    mcp.add_tool(
        capability.func,
        name=f"{capability.category}.{capability.name}",
        description=_description(capability),
        meta=frontdoor.tool_ui_meta.get(f"{capability.category}.{capability.name}"),  # HomeShed Pro's cards
    )

for _uri, (_name, _html) in frontdoor.ui_resources.items():  # HomeShed Pro's approval cards (MCP Apps)
    mcp.resource(_uri, name=_name, mime_type=frontdoor.UI_MIME, meta={"ui": {"prefersBorder": True}})(_html)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    """Cheap liveness check for the dashboard (it used /capabilities, which does real work) and uptime monitors.
    The one route that needs no token (still the Host check): it says only that the server is up."""
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/capabilities", methods=["GET"])
async def list_capabilities(request: Request) -> JSONResponse:
    """Discovery — what capabilities exist. Never executes anything (nextsteps.md Phase 5).

    custom_route bypasses the SDK's own auth/transport-security by design (its docstring says
    routes registered this way are meant for public/OAuth use) — RequestGuard below is what
    actually protects this route, both the Host check and the bearer token.
    """
    return JSONResponse(load_manifests())


@mcp.custom_route("/activity", methods=["GET"])
async def list_activity(request: Request) -> JSONResponse:
    """Recent real capability invocations, newest-first. In-memory only (resets on restart) —
    a live feed for dashboards, not an audit trail. Same RequestGuard protection as /capabilities.
    """
    return JSONResponse(get_activity_log())


@mcp.custom_route("/settings", methods=["GET", "PUT"])
async def tool_settings(request: Request) -> JSONResponse:
    """Behaviour settings that change without a restart (runtime_settings.py): values plus
    the schema an admin client draws its form from. Owner token only (client tokens only reach /mcp)."""
    import runtime_settings

    if request.method == "PUT":
        try:
            return JSONResponse({"values": runtime_settings.update(await request.json())})
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"values": runtime_settings.values(), "schema": runtime_settings.SCHEMA})


@mcp.custom_route("/connections", methods=["GET"])
async def list_connections(request: Request) -> JSONResponse:
    """Callers (owner or client name) active in the last 15 minutes, one row per caller per machine,
    plus the names of the clients that exist now (deleted ones keep their usage history but aren't
    shown), for an admin client's live view. Owner token only."""
    names = [c["name"] for c in clients.list_clients()["clients"]]
    return JSONResponse({"connections": clients.active_connections(), "clients": names,
                         "window_s": clients.CONNECTION_WINDOW_S})


@mcp.custom_route("/local-ai/backends", methods=["GET"])
async def local_ai_backends(request: Request) -> JSONResponse:
    """The delegator's live model list (local_ai_backends.json + gateway discovery), so the
    dashboard shows exactly the models local_ai.ask uses, with no second list to maintain. Names,
    tiers and status only: never URLs or keys."""
    import time as _time

    from tools.local_ai import ask as _ask

    now = _time.time()
    try:
        pool = _ask.backends()
    except _ask.LocalAIError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"backends": [
        {"name": b["name"], "aliases": b["aliases"], "cloud": b["cloud"], "tier": b["tier"],
         "configured": b["configured"], "benched": _ask._benched_until.get(b["name"], 0) > now,
         "retry_in_s": _ask.bench_remaining(b["name"])}
        for b in sorted(pool, key=lambda b: (b["tier"], b["name"]))]})


@mcp.custom_route("/local-ai/decisions", methods=["GET"])
async def local_ai_decisions(request: Request) -> JSONResponse:
    """The delegator's recent choices, newest first: which model answered, which were skipped and
    why, timings. Names and reasons only, never prompts or answers. For the dashboard's
    "Why this model?" view. Same RequestGuard protection as /capabilities."""
    from tools.local_ai import ask as _ask

    try:
        limit = max(1, min(int(request.query_params.get("limit", 20)), _ask.DECISIONS_MAX))
    except ValueError:
        limit = 20
    return JSONResponse({"decisions": _ask.decisions(limit), "bench_s": _ask.BENCH_S})


@mcp.custom_route("/local-ai/backends/{name}/reset", methods=["POST"])
async def local_ai_reset_bench(request: Request) -> JSONResponse:
    """Lift a benched model's cooldown now (e.g. after fixing it), so the delegator tries it on the
    next call instead of waiting out BENCH_S. Changes nothing else. Same RequestGuard protection."""
    from tools.local_ai import ask as _ask

    name = request.path_params["name"]
    if name not in {b["name"] for b in _ask.backends()}:
        return JSONResponse({"error": f"unknown model {name!r}"}, status_code=404)
    return JSONResponse({"name": name, "was_benched": _ask.reset_bench(name)})


@mcp.custom_route("/usage", methods=["GET"])
async def usage_summary(request: Request) -> JSONResponse:
    """Approximate Claude-token savings (usage.py): sizes-only counters, labelled estimates.
    Read by the dashboard's token strip. Same RequestGuard protection as /capabilities."""
    import usage

    return JSONResponse(usage.summary())


@mcp.custom_route("/tools/status", methods=["GET"])
async def tools_status(request: Request) -> JSONResponse:
    """Per-tool status for the dashboard's Tools page: on/off, scope, lifetime calls/errors and
    last use (usage.py), recent speed (activity feed). Names and numbers only. Same RequestGuard
    protection as /capabilities."""
    import toolswitch
    import usage
    from registry import all_capabilities

    manifests = {m["id"]: m for m in load_manifests()}
    counts = usage.summary().get("capabilities", {})
    recent: dict[str, list[dict]] = {}
    for e in get_activity_log():
        if not e.get("refused"):
            recent.setdefault(e["id"], []).append(e)
    # why_disabled covers both the owner's switches and the tools that ship off (ENABLE_TOOLS): a default-off tool
    # must show as off here, or the Tools page would call it on while every call is refused.
    why = {f"{c.category}.{c.name}": toolswitch.why_disabled(f"{c.category}.{c.name}") for c in all_capabilities()}
    off = {cid for cid, reason in why.items() if reason}
    client_names = [c["name"] for c in clients.list_clients()["clients"]]
    rows = []
    for cap in all_capabilities():
        cid = f"{cap.category}.{cap.name}"
        m, u, r = manifests.get(cid, {}), counts.get(cid, {}), recent.get(cid, [])
        calls, errors = u.get("calls", 0), u.get("errors", 0)
        rows.append({
            "id": cid, "category": cap.category, "risk": m.get("risk", "read"),
            "scope": m.get("scope") or f"{m.get('risk', 'read')}:{cap.category}",
            "description": m.get("description", ""), "enabled": cid not in off, "off_reason": why.get(cid),
            "calls": calls, "errors": errors,
            "success_rate": round((calls - errors) / calls, 4) if calls else None,
            "avg_ms": round(sum(e["duration_ms"] for e in r) / len(r), 1) if r else None,
            "last_call": u.get("last") or max((e["timestamp"] for e in r), default=None),
            "clients": {n: clients.is_allowed(n, cid, m.get("risk", "read"), cap.category) for n in client_names},
            # the Run page builds its form from these (to-do #50): name -> {type, required, default}
            "inputs": {k: {f: v[f] for f in ("type", "required", "default") if f in v}
                       for k, v in (m.get("inputs") or {}).items() if isinstance(v, dict)},
        })
    return JSONResponse({"tools": sorted(rows, key=lambda x: x["id"]), "switched_off": sorted(off)})


@mcp.custom_route("/tools/{tool_id}/{action}", methods=["POST"])
async def tools_switch(request: Request) -> JSONResponse:
    """Switch one tool on or off (the Tools page's switches). An internal kill-switch, not customer
    access control. Same RequestGuard protection as /capabilities."""
    import toolswitch
    from registry import all_capabilities

    tool_id, action = request.path_params["tool_id"], request.path_params["action"]
    if action not in ("enable", "disable"):
        return JSONResponse({"error": "action must be enable or disable"}, status_code=400)
    if tool_id not in {f"{c.category}.{c.name}" for c in all_capabilities()}:
        return JSONResponse({"error": f"unknown tool {tool_id!r}"}, status_code=404)
    changed = toolswitch.set_enabled(tool_id, action == "enable")
    return JSONResponse({"id": tool_id, "enabled": action == "enable", "changed": changed})


# --- client management (owner token only: RequestGuard keeps client tokens on /mcp) ---------------

def _client_error(exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/projects", methods=["GET"])
async def projects_list(request: Request) -> JSONResponse:
    """Which folder uses which memory agent (projects.py). Owner only: client tokens stop at /mcp."""
    return JSONResponse(projects.list_projects())


@mcp.custom_route("/projects", methods=["POST"])
async def projects_register(request: Request) -> JSONResponse:
    """JSON {path, name, agent}: that folder's sessions use that memory agent."""
    try:
        body = await _body(request)
        row = projects.register(str(body.get("path", "")), str(body.get("name", "")), str(body.get("agent", "")))
    except (projects.ProjectError, ValueError, TypeError, AttributeError) as exc:
        return _client_error(exc)
    return JSONResponse(row)


MEMORY_PANEL_MAX = 100


def _panel_facts(category: str, limit: int, max_chars: int) -> dict:
    """One category of the shared memory's facts, straight from the configured store (the built-in one or
    memory-core). A direct call: only calls through the served capability are logged, so the panel's polling never
    shows up as tool calls."""
    from tools.memory.capture import facts_session_id
    from tools.memory.recall import recall
    return recall(facts_session_id(category), limit=limit, max_chars=max_chars)


@mcp.custom_route("/memory/facts", methods=["GET"])
async def memory_facts_route(request: Request) -> JSONResponse:
    """?category=&limit=&max_chars=: one category's facts, newest first. The optional panel's Memory card reads this
    when it has no memory-core address of its own, so a new install's built-in memory shows (the prepper's clean-VM
    test, 2026-10-01). Owner only: client tokens stop at /mcp."""
    from tools.memory.capture import MemoryError
    q = request.query_params
    try:
        category = str(q.get("category", "general"))
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", category):
            raise ValueError("category: lower-case letters, digits and dashes")
        limit = max(1, min(int(q.get("limit", "50")), MEMORY_PANEL_MAX))
        max_chars = max(0, min(int(q.get("max_chars", "300")), 2000))
    except (ValueError, TypeError) as exc:
        return _client_error(exc)
    try:
        return JSONResponse(await asyncio.to_thread(_panel_facts, category, limit, max_chars))
    except MemoryError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@mcp.custom_route("/projects/remove", methods=["POST"])
async def projects_remove(request: Request) -> JSONResponse:
    """JSON {path}: that folder's sessions go back to the shared memory."""
    try:
        body = await _body(request)
        return JSONResponse({"removed": projects.remove(str(body.get("path", "")))})
    except (projects.ProjectError, ValueError, TypeError, AttributeError) as exc:
        return _client_error(exc)


@mcp.custom_route("/clients", methods=["GET"])
async def clients_list(request: Request) -> JSONResponse:
    """Clients (no token hashes), per-client usage, and the admin audit trail."""
    import usage
    out = clients.list_clients()
    per_client = usage.summary().get("clients", {})
    for c in out["clients"]:
        c["usage"] = per_client.get(c["name"], {})
    out["audit"] = clients.audit_trail(40)
    return JSONResponse(out)


@mcp.custom_route("/clients", methods=["POST"])
async def clients_create(request: Request) -> JSONResponse:
    try:
        body = await _body(request)  # a JSON list used to reach body.get and answer 500 (the prepper's VM test)
        token = clients.create(str(body.get("name", "")), str(body.get("note", "")), str(body.get("preset", "none")),
                               body.get("expires_days"), body.get("rate_per_min", clients.DEFAULT_RATE_PER_MIN))
    except (clients.ClientError, ValueError, TypeError) as exc:
        return _client_error(exc)
    return JSONResponse({"name": body["name"], "token": token})


@mcp.custom_route("/clients/suspend-all", methods=["POST"])
async def clients_suspend_all(request: Request) -> JSONResponse:
    try:
        return JSONResponse({"suspended": clients.suspend_all()})
    except clients.ClientError as exc:
        return _client_error(exc)


@mcp.custom_route("/clients/{name}", methods=["DELETE"])
async def clients_delete(request: Request) -> JSONResponse:
    try:
        clients.delete(request.path_params["name"])
    except clients.ClientError as exc:
        return _client_error(exc)
    return JSONResponse({"deleted": request.path_params["name"]})


@mcp.custom_route("/clients/{name}/{action}", methods=["POST"])
async def clients_action(request: Request) -> JSONResponse:
    """rotate | suspend | resume | view (JSON tool_view, images) | limits (JSON rate_per_min, expires_days) | spend-cap (JSON usd, or null) | memory (JSON agent_id, or null: its own,
    made on first use) | shared-read-on|off (reading the shared memory) | preset-none|read|all."""
    name, action = request.path_params["name"], request.path_params["action"]
    try:
        if action == "rotate":
            return JSONResponse({"name": name, "token": clients.rotate(name)})
        if action in ("suspend", "resume"):
            clients.set_suspended(name, action == "suspend")
        elif action == "limits":
            body = await _body(request)
            clients.set_limits(name, body.get("rate_per_min"), body.get("expires_days"))
        elif action == "spend-cap":  # JSON usd: dollars a day, or null for no cap of its own
            body = await _body(request)
            clients.set_spend_cap(name, body.get("usd"))
        elif action == "memory":
            body = await _body(request)
            clients.set_memory_agent(name, body.get("agent_id"))
        elif action in ("shared-read-on", "shared-read-off"):
            clients.set_memory_shared_read(name, action == "shared-read-on")
        elif action == "view":  # JSON tool_view (full|front_door) and/or images (inline|content): frontdoor.py
            body = await _body(request)
            clients.set_view(name, body.get("tool_view"), body.get("images"))
        elif action == "toolset":  # JSON patterns: a list, or null for the whole grant (HomeShed Pro applies it)
            body = await _body(request)
            clients.set_toolset(name, body.get("patterns"))
        elif action.startswith("preset-"):
            clients.set_preset(name, action[len("preset-"):])
        else:
            return JSONResponse({"error": f"unknown action {action!r}"}, status_code=400)
    except (clients.ClientError, ValueError, TypeError) as exc:
        return _client_error(exc)
    return JSONResponse({"name": name, "done": action})


@mcp.custom_route("/oauth/pending", methods=["GET"])
async def oauth_pending(request: Request) -> JSONResponse:
    """Sign-ins waiting for the owner (oauth_server.py), and whether sign-in is set up here at all."""
    import oauth_server
    url = oauth_server.public_url()  # the panel's connect snippets show it (an address, not a secret)
    return JSONResponse({"enabled": bool(url), "public_url": url, "pending": oauth_server.pending()})


@mcp.custom_route("/oauth/pending/{pid}/{action}", methods=["POST"])
async def oauth_decide(request: Request) -> JSONResponse:
    """approve (JSON app: a new or existing app name, preset: none|read|all) | decline."""
    import oauth_server
    action = request.path_params["action"]
    if action not in ("approve", "decline"):
        return JSONResponse({"error": f"unknown action {action!r}"}, status_code=400)
    try:
        body = await _body(request) if action == "approve" else {}
        out = oauth_server.decide(request.path_params["pid"], action == "approve", str(body.get("app") or ""),
                                  str(body.get("preset") or "read"), trust=body.get("trust") is True)
    except (oauth_server.OAuthError, clients.ClientError, ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(out)


@mcp.custom_route("/oauth/trusted", methods=["GET"])
async def oauth_trusted(request: Request) -> JSONResponse:
    """Browsers the owner trusted to sign an app in without asking (oauth_server.trusted)."""
    import oauth_server
    return JSONResponse({"trusted": oauth_server.trusted()})


@mcp.custom_route("/oauth/trusted/{tid}/remove", methods=["POST"])
async def oauth_untrust(request: Request) -> JSONResponse:
    import oauth_server
    if not oauth_server.untrust(request.path_params["tid"]):
        return JSONResponse({"error": "no trusted browser with that id"}, status_code=404)
    return JSONResponse({"removed": request.path_params["tid"]})


@mcp.custom_route("/memory/pending", methods=["GET"])
async def memory_pending_list(request: Request) -> JSONResponse:
    """Apps' writes to the shared memory, waiting for the owner (memory_pending.py). Owner only, as every custom route."""
    import memory_pending
    try:
        return JSONResponse({"items": memory_pending.list_pending(), "most_per_app": memory_pending.MAX_PER_APP})
    except memory_pending.PendingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@mcp.custom_route("/memory/pending/{item_id}/{action}", methods=["POST"])
async def memory_pending_decide(request: Request) -> JSONResponse:
    """approve (the write goes into the shared memory as the owner's) | decline (it's dropped)."""
    import memory_pending
    from tools.memory.capture import MemoryError as MemoryStoreError
    item_id, action = request.path_params["item_id"], request.path_params["action"]
    if action not in ("approve", "decline"):
        return JSONResponse({"error": f"unknown action {action!r}"}, status_code=400)
    try:
        return JSONResponse(await asyncio.to_thread(memory_pending.decide, item_id, action == "approve"))
    except memory_pending.PendingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except MemoryStoreError as exc:  # the write failed: it stays waiting
        return JSONResponse({"error": f"not written, still waiting: {exc}"}, status_code=502)


@mcp.custom_route("/etsy/connect", methods=["GET", "POST"])
async def etsy_connect_route(request: Request) -> JSONResponse:
    """Connect Etsy (tools/etsy/connect.py). GET: status (names and yes/no only). POST {"redirect_uri"}: Etsy's sign-in
    address. Owner only, as every custom route."""
    from tools.commerce._http import CommerceError
    from tools.etsy import connect as etsy_connect
    try:
        if request.method == "GET":
            return JSONResponse(etsy_connect.status())
        body = await request.json()
        return JSONResponse(etsy_connect.start(str((body or {}).get("redirect_uri") or "")))
    except ValueError:
        return JSONResponse({"error": "send JSON: {\"redirect_uri\": ...}"}, status_code=400)
    except CommerceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/etsy/finish", methods=["POST"])
async def etsy_finish_route(request: Request) -> JSONResponse:
    """{"code", "state"} from the panel's callback, or {"url"}: the address Etsy sent the browser to, pasted."""
    from tools.commerce._http import CommerceError
    from tools.etsy import connect as etsy_connect
    try:
        body = await request.json() or {}
        if body.get("url"):
            result = await asyncio.to_thread(etsy_connect.finish_url, str(body["url"]))
        else:
            result = await asyncio.to_thread(etsy_connect.finish, str(body.get("code") or ""), str(body.get("state") or ""))
        return JSONResponse(result)
    except (ValueError, AttributeError):
        return JSONResponse({"error": "send JSON: {\"code\", \"state\"} or {\"url\"}"}, status_code=400)
    except CommerceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/threads/connect", methods=["GET", "POST"])
async def threads_connect_route(request: Request) -> JSONResponse:
    """Connect Threads (tools/threads/_client.py), same shape as /etsy/connect. Owner only."""
    from tools.commerce._http import CommerceError
    from tools.threads import _client as threads_client
    try:
        if request.method == "GET":
            return JSONResponse(threads_client.status())
        body = await request.json()
        return JSONResponse(threads_client.start(str((body or {}).get("redirect_uri") or "")))
    except ValueError:
        return JSONResponse({"error": "send JSON: {\"redirect_uri\": ...}"}, status_code=400)
    except CommerceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/threads/finish", methods=["POST"])
async def threads_finish_route(request: Request) -> JSONResponse:
    from tools.commerce._http import CommerceError
    from tools.threads import _client as threads_client
    try:
        body = await request.json() or {}
        if body.get("url"):
            result = await asyncio.to_thread(threads_client.finish_url, str(body["url"]))
        else:
            result = await asyncio.to_thread(threads_client.finish, str(body.get("code") or ""), str(body.get("state") or ""))
        return JSONResponse(result)
    except (ValueError, AttributeError):
        return JSONResponse({"error": "send JSON: {\"code\", \"state\"} or {\"url\"}"}, status_code=400)
    except CommerceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/instagram/connect", methods=["GET", "POST"])
async def instagram_connect_route(request: Request) -> JSONResponse:
    """Connect Instagram (tools/instagram/_client.py), same shape as /threads/connect. Owner only."""
    from tools.commerce._http import CommerceError
    from tools.instagram import _client as instagram_client
    try:
        if request.method == "GET":
            return JSONResponse(instagram_client.status())
        body = await request.json()
        return JSONResponse(instagram_client.start(str((body or {}).get("redirect_uri") or "")))
    except ValueError:
        return JSONResponse({"error": "send JSON: {\"redirect_uri\": ...}"}, status_code=400)
    except CommerceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/instagram/finish", methods=["POST"])
async def instagram_finish_route(request: Request) -> JSONResponse:
    from tools.commerce._http import CommerceError
    from tools.instagram import _client as instagram_client
    try:
        body = await request.json() or {}
        if body.get("url"):
            result = await asyncio.to_thread(instagram_client.finish_url, str(body["url"]))
        else:
            result = await asyncio.to_thread(instagram_client.finish, str(body.get("code") or ""),
                                             str(body.get("state") or ""))
        return JSONResponse(result)
    except (ValueError, AttributeError):
        return JSONResponse({"error": "send JSON: {\"code\", \"state\"} or {\"url\"}"}, status_code=400)
    except CommerceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/pinterest/connect", methods=["GET", "POST"])
async def pinterest_connect_route(request: Request) -> JSONResponse:
    """Connect Pinterest (tools/pinterest/_client.py), same shape as /threads/connect. Owner only."""
    from tools.commerce._http import CommerceError
    from tools.pinterest import _client as pinterest_client
    try:
        if request.method == "GET":
            return JSONResponse(pinterest_client.status())
        body = await request.json()
        return JSONResponse(pinterest_client.start(str((body or {}).get("redirect_uri") or "")))
    except ValueError:
        return JSONResponse({"error": "send JSON: {\"redirect_uri\": ...}"}, status_code=400)
    except CommerceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/pinterest/finish", methods=["POST"])
async def pinterest_finish_route(request: Request) -> JSONResponse:
    from tools.commerce._http import CommerceError
    from tools.pinterest import _client as pinterest_client
    try:
        body = await request.json() or {}
        if body.get("url"):
            result = await asyncio.to_thread(pinterest_client.finish_url, str(body["url"]))
        else:
            result = await asyncio.to_thread(pinterest_client.finish, str(body.get("code") or ""),
                                             str(body.get("state") or ""))
        return JSONResponse(result)
    except (ValueError, AttributeError):
        return JSONResponse({"error": "send JSON: {\"code\", \"state\"} or {\"url\"}"}, status_code=400)
    except CommerceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/inbox", methods=["GET"])
async def inbox_list(request: Request) -> JSONResponse:
    """Agents' messages waiting for the owner (owner_inbox.py), for the panel's Messages pill. Owner only, as every
    custom route."""
    import owner_inbox
    try:
        return JSONResponse({"items": owner_inbox.list_open()})
    except owner_inbox.InboxError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@mcp.custom_route("/inbox/{item_id}/{action}", methods=["POST"])
async def inbox_answer(request: Request) -> JSONResponse:
    """reply ({"text", "done"?}) | done. The agent reads the reply with inbox.replies."""
    import owner_inbox
    item_id, action = request.path_params["item_id"], request.path_params["action"]
    if action not in ("reply", "done"):
        return JSONResponse({"error": f"unknown action {action!r}"}, status_code=400)
    try:
        if action == "done":
            return JSONResponse(owner_inbox.mark_done(item_id))
        body = await request.json()
        return JSONResponse(owner_inbox.reply(item_id, body.get("text"), bool(body.get("done"))))
    except ValueError:
        return JSONResponse({"error": "send JSON: {\"text\": ...}"}, status_code=400)
    except owner_inbox.InboxError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404 if "no message" in str(exc) else 400)


@mcp.custom_route("/proxmox/pending", methods=["GET"])
async def proxmox_pending_list(request: Request) -> JSONResponse:
    """Proxmox changes (power, snapshot, create) waiting for the owner (proxmox_pending.py). Owner only, as every
    custom route: RequestGuard refuses client tokens anywhere but /mcp."""
    import proxmox_pending
    try:
        return JSONResponse({"items": proxmox_pending.list_pending(), "most": proxmox_pending.MAX_TOTAL,
                             "expire_after_h": proxmox_pending.TTL_S // 3600})
    except proxmox_pending.PendingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@mcp.custom_route("/proxmox/pending/{item_id}/{action}", methods=["POST"])
async def proxmox_pending_decide(request: Request) -> JSONResponse:
    """approve (checked again, then sent to Proxmox; returns its task id) | decline (dropped). Never deletes anything."""
    import proxmox_pending
    from tools.proxmox._client import ProxmoxError
    item_id, action = request.path_params["item_id"], request.path_params["action"]
    if action not in ("approve", "decline"):
        return JSONResponse({"error": f"unknown action {action!r}"}, status_code=400)
    try:
        return JSONResponse(await asyncio.to_thread(proxmox_pending.decide, item_id, action == "approve"))
    except proxmox_pending.PendingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ProxmoxError as exc:  # it stays waiting, with the error
        return JSONResponse({"error": f"not done, still waiting: {exc}"}, status_code=502)


@mcp.custom_route("/proxmox/monitor", methods=["GET"])
async def proxmox_monitor_route(request: Request) -> JSONResponse:
    """The Control Panel's Proxmox tab (proxmox_monitor.py): nodes, storage, guests and alarms. Owner only, as every
    custom route. Read-only."""
    import proxmox_monitor
    from tools.proxmox._client import ProxmoxError
    try:
        return JSONResponse(await asyncio.to_thread(proxmox_monitor.snapshot))
    except ProxmoxError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@mcp.custom_route("/proxmox/monitor/history", methods=["GET"])
async def proxmox_monitor_history(request: Request) -> JSONResponse:
    """?kind=node|qemu|lxc&node=&vmid=&timeframe=hour|day|week|month|year: Proxmox's own graph points. Read-only."""
    import proxmox_monitor
    from tools.proxmox._client import ProxmoxError
    q = request.query_params
    try:
        vmid = int(q["vmid"]) if q.get("vmid") else None
    except ValueError:
        return JSONResponse({"error": "vmid must be a number"}, status_code=400)
    kind = q.get("kind") or "node"
    if kind not in ("node", "qemu", "lxc"):
        return JSONResponse({"error": "kind must be node, qemu or lxc"}, status_code=400)
    try:
        return JSONResponse(await asyncio.to_thread(proxmox_monitor.history, kind, q.get("node") or "", vmid,
                                                    q.get("timeframe") or "hour"))
    except ProxmoxError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400 if "must" in str(exc) else 502)


@mcp.custom_route("/printify/pending", methods=["GET"])
async def printify_pending_list(request: Request) -> JSONResponse:
    """Printify publishes waiting for the owner (printify_pending.py, to-do #59). Owner only, as every custom route."""
    import printify_pending
    try:
        return JSONResponse({"items": printify_pending.list_pending(), "most": printify_pending.MAX_TOTAL,
                             "expire_after_h": printify_pending.TTL_S // 3600,
                             "published_last_24h": len(printify_pending.published_today()),
                             "daily_max": printify_pending.daily_max()})
    except printify_pending.PendingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@mcp.custom_route("/printify/pending/{item_id}/{action}", methods=["POST"])
async def printify_pending_decide(request: Request) -> JSONResponse:
    """approve (checked again, then published to the shop) | decline (dropped)."""
    import printify_pending
    from tools.commerce._http import CommerceError
    item_id, action = request.path_params["item_id"], request.path_params["action"]
    if action not in ("approve", "decline"):
        return JSONResponse({"error": f"unknown action {action!r}"}, status_code=400)
    try:
        return JSONResponse(await asyncio.to_thread(printify_pending.decide, item_id, action == "approve"))
    except printify_pending.PendingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except CommerceError as exc:  # it stays waiting, with the error
        return JSONResponse({"error": f"not published, still waiting: {exc}"}, status_code=502)


@mcp.custom_route("/approvals/{item_id}", methods=["GET"])
async def approvals_status(request: Request) -> JSONResponse:
    """One request's status across the memory, Proxmox, commerce and inbox queues (approvals.py). Owner only, as every
    custom route; apps ask with approvals.status. Read-only."""
    import approvals
    found = await asyncio.to_thread(approvals.status, request.path_params["item_id"], None)
    return JSONResponse(found, status_code=404 if found["status"] == "unknown" else 200)


@mcp.custom_route("/clients/{name}/tools/{tool_id}/{action}", methods=["POST"])
async def clients_tool(request: Request) -> JSONResponse:
    """Grant or revoke one tool for one client."""
    name, tool_id, action = (request.path_params[k] for k in ("name", "tool_id", "action"))
    from registry import all_capabilities
    if tool_id not in {f"{c.category}.{c.name}" for c in all_capabilities()}:
        return JSONResponse({"error": f"unknown tool {tool_id!r}"}, status_code=404)
    try:
        if action == "grant":
            clients.grant(name, tool_id, *_tool_meta(tool_id))
        elif action == "revoke":
            clients.revoke(name, tool_id, *_tool_meta(tool_id))
        else:
            return JSONResponse({"error": "action must be grant or revoke"}, status_code=400)
    except clients.ClientError as exc:
        return _client_error(exc)
    return JSONResponse({"name": name, "tool": tool_id, "done": action})


# --- Credentials vault (vault.py). Owner token only: RequestGuard refuses client tokens anywhere but /mcp.
VAULT_REVEAL_KEY = os.environ.get("VAULT_REVEAL_KEY", "")
VAULT_META = ("label", "kind", "service", "username", "host", "port", "note", "used_by")


def _vault_error(exc: Exception) -> JSONResponse:
    known = isinstance(exc, (vault.VaultError, ValueError)) and not isinstance(exc, json.JSONDecodeError)
    return JSONResponse({"error": str(exc) if known else "bad request"}, status_code=400)


async def _body(request: Request) -> dict:
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    if not isinstance(body, dict):
        raise ValueError("the body must be a JSON object")
    return body


@mcp.custom_route("/vault", methods=["GET"])
async def vault_list(request: Request) -> JSONResponse:
    """Every credential's details (never a value), whether the vault is on, and the recent audit trail."""
    return JSONResponse({"enabled": vault.enabled(), "reveal": bool(VAULT_REVEAL_KEY),
                         "credentials": vault.list_credentials(), "audit": vault.audit_trail(30)})


@mcp.custom_route("/vault/{name}", methods=["PUT"])
async def vault_set(request: Request) -> JSONResponse:
    """Add or replace a credential (with "value"), or change only its details (without)."""
    try:
        body = await _body(request)
        meta = {k: body[k] for k in VAULT_META if k in body}
        out = vault.set_credential(request.path_params["name"], body.get("value"),
                                   actor=str(body.get("by") or "owner")[:40], **meta)
    except (vault.VaultError, ValueError, TypeError) as exc:
        return _vault_error(exc)
    return JSONResponse(out)


@mcp.custom_route("/vault/{name}", methods=["DELETE"])
async def vault_delete(request: Request) -> JSONResponse:
    try:
        vault.delete_credential(request.path_params["name"], actor="owner")
    except vault.VaultError as exc:
        return _vault_error(exc)
    return JSONResponse({"name": request.path_params["name"], "deleted": True})


# HomeShed Pro: the panel's Pro card and the known-bugs feed (pro_routes.py, public since 2026-10-01).
import pro_routes  # noqa: E402

pro_routes.register(mcp, _body)

# The owner's private add-on routes, when private_routes.py is present.
if private_routes is not None:
    private_routes.register(mcp, _body, AUTH_TOKEN)


@mcp.custom_route("/vault/{name}/generate", methods=["POST"])
async def vault_generate(request: Request) -> JSONResponse:
    """A new random value for a secret this platform invents itself. Returns details only, never the value."""
    try:
        body = await _body(request)
        meta = {k: body[k] for k in VAULT_META if k in body}
        out = vault.generate(request.path_params["name"], actor=str(body.get("by") or "owner")[:40],
                             nbytes=int(body.get("bytes", 32)), **meta)
    except (vault.VaultError, ValueError, TypeError) as exc:
        return _vault_error(exc)
    return JSONResponse(out)


_reveal_misses: deque = deque()  # when wrong reveal keys arrived
REVEAL_MAX_MISSES, REVEAL_WINDOW_S = 5, 600


def _reveal_refusal(request: Request, name: str) -> JSONResponse | None:
    """None when the request holds the reveal key, else the answer to send. Wrong keys are counted and audited: after
    5 in 10 minutes every reveal is refused until the window clears (the prepper's VM test: 25 wrong keys all got
    403 at full speed). Sending no key at all is the ordinary case, not a guess."""
    import hmac

    now = time.time()
    while _reveal_misses and now - _reveal_misses[0] > REVEAL_WINDOW_S:
        _reveal_misses.popleft()
    if len(_reveal_misses) >= REVEAL_MAX_MISSES:
        return JSONResponse({"error": "too many wrong reveal keys: try again in 10 minutes"}, status_code=429)
    given = request.headers.get("x-vault-reveal", "")
    if VAULT_REVEAL_KEY and hmac.compare_digest(given.encode(), VAULT_REVEAL_KEY.encode()):
        return None
    if given:
        _reveal_misses.append(now)
        vault.note_wrong_reveal_key(name)
    return JSONResponse({"error": "revealing a value needs the reveal key"}, status_code=403)


@mcp.custom_route("/vault/{name}/reveal", methods=["POST"])
async def vault_reveal(request: Request) -> JSONResponse:
    """The plain value, only with the reveal key. The dashboard holds it and sends it after re-checking the owner's
    password; no Claude session holds it, so even the owner token can't read a value back through here."""
    name = request.path_params["name"]
    refused = _reveal_refusal(request, name)
    if refused is not None:
        return refused
    try:
        return JSONResponse({"name": name, "value": vault.reveal(name, actor="owner (admin client)")})
    except vault.VaultError as exc:
        return _vault_error(exc)


NVIDIA_URL = "https://integrate.api.nvidia.com/v1"


def _saved_for(name: str, url: str) -> bool:
    """Is url the address the vault entry `name` was saved for: its "host" detail (a host name or an address), and its
    "port" when one is saved?"""
    entry = next((c for c in vault.list_credentials() if c["name"] == name), {})
    saved = str(entry.get("host") or "").strip()
    if not saved:
        return False
    try:
        want, have = urlsplit(url), urlsplit(saved if "://" in saved else f"//{saved}")
        port = entry.get("port") or have.port
        if not want.hostname or want.hostname.lower() != (have.hostname or "").lower():
            return False
        return not port or int(port) == (want.port or (443 if want.scheme == "https" else 80))
    except (ValueError, TypeError):
        return False


@mcp.custom_route("/vault-test", methods=["POST"])
async def vault_test(request: Request) -> JSONResponse:
    """Check a connection before saving it (an admin client's Connect button). Presets: "nvidia" (NVIDIA's cloud
    models), "openai" (any OpenAI-compatible server: Ollama, llama.cpp, LM Studio, a gateway), "tcp" (can this server
    reach host:port, e.g. SSH). The key comes with the request (not saved yet) or from a vault name, and is never
    sent back."""
    import asyncio

    import httpx

    try:
        body = await _body(request)
        preset = str(body.get("preset", ""))
        typed, name = str(body.get("key") or ""), str(body.get("name") or "")
        if name and not typed and name not in {c["name"] for c in vault.list_credentials()}:
            raise ValueError(f"{name} isn't in the vault")  # never an environment variable of the same name
        key = typed or (vault.secret(name) or "" if name else "")
        if preset in ("nvidia", "openai"):
            url = str(body.get("url") or (NVIDIA_URL if preset == "nvidia" else "")).strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise ValueError("the address must start with http:// or https://")
            # A saved key goes only where it was saved for: NVIDIA's own address, or the host saved with the entry.
            # Anywhere else needs the key typed into the request, or the reveal key (R&D F1, 2026-09-30: any holder
            # of the owner token could send any secret to an address of their choice, and the prepper's listener
            # received one on the clean VM).
            if key and not typed and not (url == NVIDIA_URL if preset == "nvidia" else _saved_for(name, url)):
                refused = _reveal_refusal(request, name)
                if refused is not None:
                    if refused.status_code == 429:
                        return refused
                    return JSONResponse({"error": "a saved key is only tested against the address it was saved "
                                                  "for: type the key to test another address"}, status_code=403)
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                r = await client.get(f"{url}/models", headers={"Authorization": f"Bearer {key}"} if key else {})
            if r.status_code in (401, 403):
                return JSONResponse({"ok": False, "detail": "The server answered, but refused the key."})
            if r.status_code != 200:
                return JSONResponse({"ok": False, "detail": f"The server answered with HTTP {r.status_code}."})
            data = r.json().get("data") if "json" in r.headers.get("content-type", "") else None
            ids = [m.get("id") for m in (data or []) if isinstance(m, dict) and m.get("id")]
            return JSONResponse({"ok": True, "detail": f"Connected: {len(ids)} models available.", "models": ids[:8]})
        if preset == "tcp":
            host, port = str(body.get("host", "")).strip(), int(body.get("port") or 0)
            if not host or not 0 < port < 65536:
                raise ValueError("a host and a port from 1 to 65535 are needed")
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
                writer.close()
            except (OSError, asyncio.TimeoutError):
                return JSONResponse({"ok": False, "detail": f"{host}:{port} doesn't answer from the tool server."})
            return JSONResponse({"ok": True, "detail": f"{host}:{port} answers."})
        raise ValueError("preset must be nvidia, openai or tcp")
    except httpx.RequestError:
        return JSONResponse({"ok": False, "detail": "Couldn't reach that address from the tool server."})
    except (vault.VaultError, ValueError, TypeError) as exc:
        return _vault_error(exc)


@mcp.custom_route("/bugs/suggested", methods=["GET"])
async def bugs_suggested(request: Request) -> JSONResponse:
    """Failed tool calls seen in 2+ sessions, waiting for the owner (tools/bugs/seen.py). Owner only, as every custom
    route."""
    from tools.bugs import seen
    try:
        return JSONResponse(seen.suggestions())
    except seen.SeenError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@mcp.custom_route("/bugs/suggested/{key}/{action}", methods=["POST"])
async def bugs_suggested_decide(request: Request) -> JSONResponse:
    """keep (it becomes a known bug; optional body {"title", "symptom"}) | dismiss (never suggested again)."""
    from tools.bugs import known, seen
    try:
        body = await _body(request)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        return JSONResponse(await asyncio.to_thread(seen.decide, request.path_params["key"], request.path_params["action"],
                                                    str(body.get("title") or ""), str(body.get("symptom") or "")))
    except (seen.SeenError, known.BugsError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/agents/roster", methods=["GET"])
async def agents_roster_list(request: Request) -> JSONResponse:
    """Who's who among the agents (agents_roster.py): every row, `brief` (the lines a session starts with) and `items`
    (the rows waiting for the owner: the Control Panel's approvals bar)."""
    import agents_roster
    try:
        return JSONResponse({**agents_roster.roster(), "brief": agents_roster.brief(), "items": agents_roster.waiting()})
    except agents_roster.RosterError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@mcp.custom_route("/agents/roster/{name}/{action}", methods=["POST"])
async def agents_roster_decide(request: Request) -> JSONResponse:
    """The owner: confirm a row (optional body {"role"}: their own wording) or remove it. Owner only, as every custom
    route; the agents' tool can only add or propose."""
    import agents_roster
    try:
        body = await _body(request)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        return JSONResponse(agents_roster.decide(request.path_params["name"], request.path_params["action"],
                                                 str(body.get("role") or "")))
    except agents_roster.RosterError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/publisher/sign", methods=["GET"])
async def publisher_sign_waiting(request: Request) -> JSONResponse:
    """Requests to sign a library script (library_signing.py), with the code: the Control Panel's approvals bar."""
    import library_signing
    try:
        return JSONResponse({"items": library_signing.waiting()})
    except library_signing.SigningError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@mcp.custom_route("/publisher/sign/{id}/{action}", methods=["POST"])
async def publisher_sign_decide(request: Request) -> JSONResponse:
    """The owner: approve (sign the content they read, if unchanged) or decline. Owner only, as every custom route."""
    import library_signing
    try:
        return JSONResponse(await asyncio.to_thread(library_signing.decide, request.path_params["id"],
                                                    request.path_params["action"]))
    except library_signing.SigningError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/bugs/export", methods=["GET"])
async def bugs_export(request: Request) -> JSONResponse:
    """The public bugs as a cleaned known-bugs pack, ready to publish on the Pro API."""
    from tools.bugs import known
    return JSONResponse(known.export_pack())


@mcp.custom_route("/observations/summary", methods=["GET"])
async def observations_summary(request: Request) -> JSONResponse:
    """Open-observation count plus up to 5 titles. Read by the local Claude Code SessionStart
    hook (.claude/hooks/observations-count.py) with one GET instead of a full MCP handshake.
    Same RequestGuard protection as /capabilities.
    """
    from tools.observe import _store

    return JSONResponse(_store.summary())


@mcp.custom_route("/observations/repeat", methods=["POST"])
async def observations_repeat(request: Request) -> JSONResponse:
    """One day of guard stops for one class of slip, posted by the daily self-review (.claude/hooks/self_review.py):
    kept on the class's observation, so repeats escalate without anyone calling observe.log (R&D's rules review v2).
    Counts, a class, a date and guard ids only: never command text. Same RequestGuard protection as /capabilities."""
    from tools.observe import _store

    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("send a JSON object")
        result = _store.record_repeat(body.get("class"), body.get("date"), body.get("stops"),
                                      sessions=body.get("sessions") or 0, retries=body.get("retries") or 0,
                                      guards=body.get("guards") if isinstance(body.get("guards"), list) else ())
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)
    return JSONResponse(result)


# A platform service that needs one route and nothing else gets its own token for that route alone, kept in the vault
# (2026-09-29): the video studio reads narration from /voice/natural, where the Gemini key stays. The studio's deploy
# script makes the token on the server and hands it to both sides; it never passes through anyone's screen.
SERVICE_ROUTES = {"/voice/natural": "STUDIO_SERVICE_TOKEN"}


def _service_allowed(path: str, auth: bytes) -> bool:
    name = SERVICE_ROUTES.get(path)
    if not name or not auth.startswith(b"Bearer "):
        return False
    import hmac

    import vault

    token = vault.secret(name) or ""
    return len(token) >= 32 and hmac.compare_digest(auth, f"Bearer {token}".encode())


OAUTH_OPEN_PATHS = ("/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource",
                    "/authorize", "/token", "/register", "/revoke", "/oauth/consent/")


def _oauth_app() -> ASGIApp | None:
    """The sign-in routes (oauth_server.py) when MCP_PUBLIC_URL is set, else None: the SDK's own OAuth handlers with
    HomeShed's provider, the resource metadata for /mcp, and the page that waits for the owner's approval."""
    import oauth_server
    base = oauth_server.public_url()
    if not base:
        return None
    from mcp.server.auth.routes import build_metadata, create_auth_routes, create_protected_resource_routes
    from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
    from pydantic import AnyHttpUrl
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse
    from starlette.routing import Route
    provider = oauth_server.Provider()
    reg, rev = ClientRegistrationOptions(enabled=True), RevocationOptions(enabled=True)
    routes = [r for r in create_auth_routes(provider, AnyHttpUrl(base), client_registration_options=reg,
                                            revocation_options=rev)
              if r.path != "/.well-known/oauth-authorization-server"]
    meta = build_metadata(AnyHttpUrl(base), None, reg, rev).model_dump(mode="json", exclude_none=True)
    # Public clients (a chat app has no secret to keep) and Claude's "published identity" (CIMD), which the SDK's
    # metadata doesn't list.
    meta["token_endpoint_auth_methods_supported"] = ["none", "client_secret_post", "client_secret_basic"]
    meta["client_id_metadata_document_supported"] = True

    async def as_meta(request: Request) -> JSONResponse:
        return JSONResponse(meta, headers={"Access-Control-Allow-Origin": "*"})

    async def consent_page(request: Request) -> HTMLResponse:
        import asyncio
        pid = request.path_params["pid"]
        item = oauth_server._load()["pending"].get(pid)
        cookie = request.cookies.get(oauth_server.trust_cookie_name(item["client_id"])) if item else None
        await asyncio.to_thread(oauth_server.consent_view, pid, cookie)
        return HTMLResponse(oauth_server.CONSENT_HTML, headers={"Cache-Control": "no-store",
                                                                "X-Frame-Options": "DENY"})

    async def consent_status(request: Request) -> JSONResponse:
        out = oauth_server.consent_status(request.path_params["pid"])
        trust = out.pop("_trust_cookie", None)
        resp = JSONResponse(out, headers={"Cache-Control": "no-store"})
        if trust:  # only this browser can sign the app in again without asking; scripts can't read it
            resp.set_cookie(trust[0], trust[1], max_age=oauth_server.TRUST_TTL_S, path="/oauth/consent",
                            secure=True, httponly=True, samesite="lax")
        return resp

    routes += [Route("/.well-known/oauth-authorization-server", as_meta, methods=["GET", "OPTIONS"]),
               Route("/oauth/consent/{pid}", consent_page, methods=["GET"]),
               Route("/oauth/consent/{pid}/status", consent_status, methods=["GET"])]
    routes += create_protected_resource_routes(AnyHttpUrl(base + "/mcp"), [AnyHttpUrl(base)],
                                               resource_name="HomeShed")
    return Starlette(routes=routes)


def _is_oauth_path(path: str) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) or path.startswith(p + "/")
               for p in OAUTH_OPEN_PATHS)


class RequestGuard:
    """Uniform gate in front of every route — MCP protocol routes and custom_route alike.

    Host header against MCP_ALLOWED_HOSTS, then bearer token against MCP_AUTH_TOKEN (or a service token on its
    one route, SERVICE_ROUTES, or a client token on /mcp only). The Host
    check duplicates TransportSecuritySettings below for /mcp, but that setting only applies to
    the SDK's own protocol routes — custom_route endpoints like /capabilities skip it entirely
    (confirmed: without this, /capabilities accepted any Host header, bearer-token-only). Doing
    it here instead makes protection uniform regardless of how a route got registered.
    """

    # Wrong tokens from one address are throttled like the panel's sign-in (R&D's security review, 2026-10-01): after
    # FAIL_MAX in FAIL_WINDOW_S that address gets 429 until the window passes. The owner token is checked first, so a
    # throttled address never locks the owner out.
    FAIL_WINDOW_S, FAIL_MAX, FAIL_KEEP = 300, 20, 1000

    def __init__(self, app: ASGIApp, token: str, allowed_hosts: list[str]):
        self.app = app
        self.token = f"Bearer {token}"
        self.allowed_hosts = set(allowed_hosts)
        self._fails: dict[str, list[float]] = {}
        self.oauth = _oauth_app()  # sign-in with OAuth, once MCP_PUBLIC_URL says where chat apps reach this server

    OAUTH_WINDOW_S, OAUTH_MAX = 600, 60  # sign-in steps (register, authorize, token, revoke) per address

    def _oauth_limited(self, ip: str, now: float) -> bool:
        """Per-address limit on the sign-in routes (R&D, 27 Oct): one address can't fill the waiting list, churn
        registrations or flood the owner's phone. A real sign-in takes about four steps."""
        calls = self._oauth_calls = getattr(self, "_oauth_calls", {})
        if len(calls) > self.FAIL_KEEP:
            calls.clear()
        recent = [t for t in calls.get(ip, []) if now - t < self.OAUTH_WINDOW_S]
        limited = len(recent) >= self.OAUTH_MAX
        calls[ip] = recent + ([] if limited else [now])
        return limited

    def _throttled(self, ip: str, now: float, failed: bool = False) -> bool:
        if len(self._fails) > self.FAIL_KEEP:  # forget quiet addresses rather than grow without end
            self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < self.FAIL_WINDOW_S}
        recent = [t for t in self._fails.get(ip, []) if now - t < self.FAIL_WINDOW_S] + ([now] if failed else [])
        self._fails[ip] = recent
        return len(recent) >= self.FAIL_MAX

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":  # the server starting and stopping: no request, nothing to check
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            # Websockets (or anything newer) used to pass straight through, skipping the host and token checks. This
            # server has no such routes, so refuse them before they reach anything (R&D's second look, R21).
            if scope["type"] == "websocket":
                await receive()  # websocket.connect
                await send({"type": "websocket.close", "code": 1008})
            return None
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode()
        if host not in self.allowed_hosts:
            response = PlainTextResponse("invalid host", status_code=421)
            return await response(scope, receive, send)
        if scope.get("path") == "/healthz" and scope.get("method") == "GET":
            # Liveness only, for uptime monitors without the token (the prepper's clean-VM test): {"status": "ok"}.
            return await self.app(scope, receive, send)
        if self.oauth is not None and _is_oauth_path(scope.get("path", "")):
            ip, now = str((scope.get("client") or ("?",))[0]), time.time()
            if scope.get("method") != "GET" or scope.get("path") == "/authorize":  # sign-in steps, not page polls
                if self._oauth_limited(ip, now):
                    response = PlainTextResponse("too many sign-in requests from this address: try again later",
                                                 status_code=429)
                    return await response(scope, receive, send)
            return await self.oauth(scope, receive, send)  # the sign-in routes check their own requests
        auth = headers.get(b"authorization", b"")
        # The owner: checked first, so normal traffic never pays for a client lookup. Constant time (R&D's review: a
        # plain == leaks how much of a guess was right through its timing).
        if hmac.compare_digest(auth, self.token.encode()):
            return await self.app(scope, receive, send)
        if _service_allowed(scope.get("path", ""), auth):
            return await self.app(scope, receive, send)
        ip, now = str((scope.get("client") or ("?",))[0]), time.time()
        if self._throttled(ip, now):
            response = PlainTextResponse("too many wrong tokens from this address: try again in a few minutes",
                                         status_code=429)
            return await response(scope, receive, send)
        client, status = clients.resolve(auth.decode("latin-1"))
        if status in ("suspended", "expired"):
            response = PlainTextResponse(f"client {client!r} is {status}", status_code=403)
            return await response(scope, receive, send)
        if status != "ok":
            self._throttled(ip, now, failed=True)
            response = PlainTextResponse("unauthorized", status_code=401)
            if self.oauth is not None and scope.get("path", "").startswith("/mcp"):  # where a chat app signs in
                import oauth_server
                response.headers["WWW-Authenticate"] = (
                    f'Bearer resource_metadata="{oauth_server.public_url()}/.well-known/oauth-protected-resource/mcp"')
            return await response(scope, receive, send)
        if not scope.get("path", "").startswith("/mcp"):
            # Client tokens reach the MCP endpoint only: never the admin routes that manage grants.
            response = PlainTextResponse("client tokens can only use the MCP endpoint", status_code=403)
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


def http_app() -> "RequestGuard":
    http_checks()
    return RequestGuard(
        mcp.streamable_http_app(
            transport_security=TransportSecuritySettings(
                allowed_hosts=ALLOWED_HOSTS,
                allowed_origins=ALLOWED_ORIGINS,
            )
        ),
        AUTH_TOKEN,
        ALLOWED_HOSTS,
    )


def serve_http(port: int | None = None) -> None:
    app = http_app()
    if private_routes is not None:
        private_routes.start()  # the add-on's background jobs
    try:  # anonymous sharing of the public known bugs: does nothing until the owner switches it on
        from tools.share import contribute
        contribute.start()
    except ImportError:
        pass
    try:  # TrendScout's daily collect (does nothing until a topic is added); not in the public copy
        import signals
        signals.start()
    except ImportError:
        pass
    import proxmox_monitor  # the Proxmox alarms, every minute (nothing without a Proxmox token)
    proxmox_monitor.start()
    import stray_watch  # a one-off `docker exec` command hogging a core for 10 minutes is stopped, and the owner told
    stray_watch.start()
    import printify_pending  # approved requests a daily limit held back run once there's room (every 10 minutes)
    printify_pending.start()
    from tools.schedule import engine as schedules  # schedule.*: the owner's and apps' timetabled read-only tools
    schedules.start()
    from tools.email import send as email_outbox  # approved emails go out within a minute
    email_outbox.start()
    # This machine only, unless MCP_BIND says otherwise (release v1, 2026-09-30: it listened on every interface). The
    # Docker image sets MCP_BIND=0.0.0.0 inside the container, and docker-compose.yml publishes the port on 127.0.0.1.
    host = os.environ.get("MCP_BIND") or "127.0.0.1"
    uvicorn.run(app, host=host, port=port or int(os.environ.get("MCP_PORT", "8765")))


if __name__ == "__main__":
    serve_http()
