"""Client identities for the tool server: the owner decides which tools each app may use.

Each consumer that isn't the owner (an app, an agent such as OpenCode, a friend's machine) gets its own name
and bearer token. The owner's admin routes grant it tools, suspend it, rotate its token, and set an expiry and a
calls-per-minute limit. The owner token (Claude Code, the
dashboard) keeps full access, unchanged.

Rules this module keeps:
- Tokens are shown once at creation/rotation and stored only as SHA-256 hashes. They are never logged.
- Fail CLOSED: if the clients file can't be read, no client token is accepted (unlike the tool
  kill-switch, which fails open). A broken grants file must never widen access.
- Client tokens only work on the MCP endpoint (enforced in server.py's RequestGuard), so a client
  can't call the admin routes that would change its own grants.
- Internal service identities only. Customers, plans and billing would live at a separate public API
  boundary, which would itself be one client here.

Grants are patterns, evaluated as allow-then-deny:
  "*" everything | "read:*" / "write:*" | "read:<category>" | "<category>.*" | an exact tool id.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from paths import data_path

CLIENTS_FILE = Path(os.environ.get("CLIENTS_FILE") or data_path("usage/clients.json"))
AUDIT_FILE = Path(os.environ.get("CLIENTS_AUDIT_FILE") or data_path("usage/client_audit.jsonl"))
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
PRESETS = {"none": [], "read": ["read:*"], "all": ["*"]}
DEFAULT_RATE_PER_MIN = 120
TOKEN_PREFIX = "hlc_"  # recognisable prefix, so secret scanners (and people) can spot a leaked one
TOUCH_EVERY_S = 60     # persist "last used" at most once a minute per client

# Set by server.py for the duration of a client's tool call, so the registry can tag activity.
current_client: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_client", default=None)
current_connection: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_connection", default=None)
# The tool a client's MCP request named (server.py checked it); nested calls such as workflow.run's steps differ.
current_tool: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_tool", default=None)

# Live connections, for an admin view of who is connected now (several clients at once). A connection is one caller (owner or client) on one machine: the newest MCP protocol,
# which Claude Code speaks, sends no session id, so MCP sessions can't be counted. In memory only.
CONNECTION_WINDOW_S = 900  # "connected now" = active in the last 15 minutes
CONNECTION_FORGET_S = 3600
CONNECTION_PRUNE_AT = 200

_lock = threading.Lock()
_connections_lock = threading.Lock()
_connections: dict[str, dict] = {}      # tag -> {caller, host, first, last, calls}
_logger = logging.getLogger(__name__)
_cache: tuple[object, dict] = (None, {"clients": {}})
_windows: dict[str, deque] = {}         # rate limiting: recent call times per client
_last_seen: dict[str, float] = {}       # in-memory last use, persisted every TOUCH_EVERY_S
_touched: dict[str, float] = {}


class ClientError(ValueError):
    """A bad client-management request. The message is safe to show in an admin client."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load() -> dict:
    """The clients file, cached until it changes. {"unreadable": True} marks a broken file (fail closed)."""
    global _cache
    try:
        st = CLIENTS_FILE.stat()
        sig = (str(CLIENTS_FILE), st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        _cache = (None, {"clients": {}})
        return _cache[1]
    except OSError:
        return {"clients": {}, "unreadable": True}
    if sig == _cache[0]:
        return _cache[1]
    try:
        data = json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("clients"), dict):
            raise ValueError("no clients object")
    except (OSError, ValueError):
        _logger.error("clients file %s is unreadable: refusing every client token until it's fixed (fail closed)", CLIENTS_FILE)
        return {"clients": {}, "unreadable": True}
    _cache = (sig, data)
    return data


def _editable() -> dict:
    data = _load()
    if data.get("unreadable"):
        raise ClientError("the clients file is unreadable; fix or remove it before changing clients")
    return copy.deepcopy(data)


def _save(data: dict) -> None:
    global _cache
    CLIENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CLIENTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, CLIENTS_FILE)
    # Cache what was just written: a rotate keeps the file's size, and inside one clock tick its mtime too, so the
    # (mtime, size) check alone kept the old token working (seen 2026-09-29 as a flaky test_rotate_invalidates_old_token).
    st = CLIENTS_FILE.stat()
    _cache = ((str(CLIENTS_FILE), st.st_mtime_ns, st.st_size), copy.deepcopy(data))


def _audit(action: str, client: str | None, **detail) -> None:
    """Admin audit trail (who changed what). Never includes a token."""
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "action": action, "client": client, **detail}) + "\n")
    except OSError:
        _logger.error("could not write the client audit trail at %s", AUDIT_FILE)


def _get(data: dict, name: str) -> dict:
    if name not in data["clients"]:
        raise ClientError(f"no client called {name!r}")
    return data["clients"][name]


def _new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


# --- management (called from owner-only routes) -----------------------------------------------

def create(name: str, note: str = "", preset: str = "none", expires_days: int | None = None,
           rate_per_min: int | None = DEFAULT_RATE_PER_MIN) -> str:
    """Create a client. Returns its token: the only time it is ever available."""
    if not NAME_RE.match(name or ""):
        raise ClientError("name must be 2-32 characters: lowercase letters, digits and dashes, starting with a letter")
    if name == "owner":  # admin views show the owner token's calls under this name
        raise ClientError("'owner' is reserved for the owner token; pick another name")
    if preset not in PRESETS:
        raise ClientError(f"preset must be one of {sorted(PRESETS)}")
    if expires_days is not None and not 1 <= int(expires_days) <= 3650:
        raise ClientError("expiry must be between 1 and 3650 days, or never")
    if rate_per_min is not None and not 1 <= int(rate_per_min) <= 100_000:
        raise ClientError("the rate limit must be between 1 and 100000 calls per minute, or unlimited")
    with _lock:
        data = _editable()
        if name in data["clients"]:
            raise ClientError(f"a client called {name!r} already exists")
        token, now = _new_token(), time.time()
        data["clients"][name] = {
            "token_sha256": _hash(token), "note": (note or "")[:200], "created": now, "token_created": now,
            "expires": now + int(expires_days) * 86400 if expires_days else None, "suspended": False,
            "allow": list(PRESETS[preset]), "deny": [], "rate_per_min": int(rate_per_min) if rate_per_min else None,
            "last_used": None, "tool_view": "front_door", "images": "content",  # new apps start on the front door
        }
        _save(data)
    _audit("created", name, preset=preset, expires_days=expires_days, rate_per_min=rate_per_min)
    return token


def rotate(name: str, by_manager: bool = False) -> str:
    """New token for a client; the old one stops working at once. Returns the new token (shown once).
    A client with "managed_by" (a seat runner's) is refused unless its manager does it: a token rotated anywhere else
    never reaches the runner, which then fails "auth" (promoter, 2026-10-15; the owner's rule)."""
    with _lock:
        data = _editable()
        c = _get(data, name)
        if c.get("managed_by") and not by_manager:
            raise ClientError(f"{name}'s key belongs to a seat runner: rotate it with {c['managed_by']} so the runner "
                              "gets the new one (here it would end the runner's key and give it nothing)")
        token = _new_token()
        c["token_sha256"], c["token_created"] = _hash(token), time.time()
        _save(data)
    _end_oauth(name)  # a new key ends its OAuth sign-ins too: rotating is how the owner cuts an app off
    _audit("token rotated", name)
    return token


def delete(name: str) -> None:
    with _lock:
        data = _editable()
        _get(data, name)
        del data["clients"][name]
        _save(data)
    _windows.pop(name, None)
    _end_oauth(name)
    _audit("deleted", name)


def _end_oauth(name: str) -> None:
    try:
        import oauth_server
        oauth_server.end_app(name)
    except Exception:  # noqa: BLE001 - logged by oauth_server; the key change itself stands
        _logger.exception("couldn't end %s's OAuth sign-ins", name)


def set_managed_by(name: str, manager: str | None) -> None:
    """Mark a client's token as managed by a script (only it may rotate), or clear the mark with None."""
    with _lock:
        data = _editable()
        c = _get(data, name)
        if manager:
            c["managed_by"] = str(manager)[:80]
        else:
            c.pop("managed_by", None)
        _save(data)
    _audit("token manager set", name, managed_by=manager)


def set_suspended(name: str, suspended: bool) -> None:
    with _lock:
        data = _editable()
        _get(data, name)["suspended"] = bool(suspended)
        _save(data)
    _audit("suspended" if suspended else "resumed", name)


def suspend_all() -> int:
    """Emergency stop: suspend every client (grants are kept). Returns how many were active."""
    with _lock:
        data = _editable()
        active = [n for n, c in data["clients"].items() if not c.get("suspended")]
        for n in active:
            data["clients"][n]["suspended"] = True
        _save(data)
    _audit("suspended all", None, count=len(active))
    return len(active)


def set_limits(name: str, rate_per_min: int | None, expires_days: int | None) -> None:
    """Change the calls-per-minute limit (None = unlimited) and expiry (days from now; None = never)."""
    if rate_per_min is not None and not 1 <= int(rate_per_min) <= 100_000:
        raise ClientError("the rate limit must be between 1 and 100000 calls per minute, or unlimited")
    if expires_days is not None and not 1 <= int(expires_days) <= 3650:
        raise ClientError("expiry must be between 1 and 3650 days, or never")
    with _lock:
        data = _editable()
        c = _get(data, name)
        c["rate_per_min"] = int(rate_per_min) if rate_per_min else None
        c["expires"] = time.time() + int(expires_days) * 86400 if expires_days else None
        _save(data)
    _audit("limits changed", name, rate_per_min=rate_per_min, expires_days=expires_days)


def set_spend_cap(name: str, usd: float | None) -> None:
    """The app's daily cap on paid images and language-model calls, in dollars (None = no cap of its own; the
    providers' own daily caps still hold). spend.py enforces it."""
    if usd is not None:
        if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not 0 <= float(usd) <= 10_000:
            raise ClientError("the spend cap must be between 0 and 10000 dollars a day, or none")
        usd = round(float(usd), 2)
    with _lock:
        data = _editable()
        c = _get(data, name)
        c["daily_spend_usd"] = usd
        _save(data)
    _audit("spend cap changed", name, daily_spend_usd=usd)


def set_cap_delegated(name: str, on: bool) -> None:
    """Whether the owner's AI sessions may set this app's spend cap with spend.cap_set (2026-10-11: a lead bot sets
    its helper bots' caps). Off by default, so an app the owner didn't pick keeps the cap the owner set."""
    with _lock:
        data = _editable()
        c = _get(data, name)
        c["cap_delegated"] = bool(on)
        _save(data)
    _audit("spend cap delegated" if on else "spend cap delegation removed", name)


def set_control_keys(name: str, key_names: list[str] | None) -> None:
    """The vault entries (by name) this app may use as its key on a private tool that calls another service for it
    (2026-10-11: a bot uses its own seat's key, never a supervisor's). None removes the binding."""
    if key_names is not None:
        if not isinstance(key_names, list) or not all(isinstance(k, str) and KEY_NAME_RE.match(k) for k in key_names):
            raise ClientError("control keys must be a list of vault entry names (A-Z, 0-9, _)")
        key_names = sorted(set(key_names))
    with _lock:
        data = _editable()
        c = _get(data, name)
        if key_names is None:
            c.pop("control_keys", None)
        else:
            c["control_keys"] = key_names
        _save(data)
    _audit("control keys bound", name, control_keys=key_names)


def control_keys_for(name: str | None) -> list[str] | None:
    """None: no binding (the tool's own rules apply); a list: only those vault entries."""
    if not name or name == "owner":
        return None
    keys = (_load()["clients"].get(name) or {}).get("control_keys")
    return list(keys) if isinstance(keys, list) else None


KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def cap_delegated(name: str) -> bool:
    return bool((_load()["clients"].get(name) or {}).get("cap_delegated"))


AGENT_RE = re.compile(r"^agt-[a-z0-9]{4,40}$")


def set_memory_agent(name: str, agent_id: str | None) -> None:
    """Which memory-core agent this client's memory.* calls use: its project's own, from the starter kit's
    bootstrap-new-project.ps1. None = its own memory, made on its first memory call (tools/memory/capture.py); an
    app never uses the shared memory as its own (the owner's decision, 2026-10-01)."""
    agent_id = str(agent_id or "").strip() or None
    if agent_id and not AGENT_RE.match(agent_id):
        raise ClientError("a memory agent id looks like agt-xxxxxxxxxx (bootstrap-new-project.ps1 prints it)")
    with _lock:
        data = _editable()
        c = _get(data, name)
        c["memory_agent"] = agent_id
        _save(data)
    _audit("memory agent", name, memory_agent=agent_id)


def memory_agent_for(name: str | None) -> str | None:
    """The memory agent set for this client, or None (the owner has none; an app without one gets its own memory,
    tools/memory/capture.py)."""
    if not name:
        return None
    agent = (_load()["clients"].get(name) or {}).get("memory_agent")
    return agent if isinstance(agent, str) and AGENT_RE.match(agent) else None


def set_memory_shared_read(name: str, on: bool) -> None:
    """Whether this app may read the shared memory (scope "global"). On unless the owner switches it off (the owner's
    decision, 2026-10-01: each app has its own memory; reading the shared one is on by default, with a switch per app;
    writing to it waits for the owner's approval, memory_pending.py)."""
    with _lock:
        data = _editable()
        _get(data, name)["memory_shared_read"] = bool(on)
        _save(data)
    _audit("shared memory reads " + ("on" if on else "off"), name)


def shared_read_for(name: str | None) -> bool:
    """True for the owner, and for an app unless the owner switched its shared-memory reads off."""
    if not name:
        return True
    return (_load()["clients"].get(name) or {}).get("memory_shared_read") is not False


def set_view(name: str, tool_view: str | None = None, images: str | None = None) -> None:
    """How this app sees the tools (frontdoor.py): "full" list or the "front_door", and pictures "inline" (base64 in
    the JSON) or as "content" (MCP image blocks). A value left as None stays as it is."""
    import frontdoor
    if tool_view is not None and tool_view not in frontdoor.VIEWS:
        raise ClientError(f"tool_view must be one of {list(frontdoor.VIEWS)}")
    if images is not None and images not in frontdoor.IMAGE_MODES:
        raise ClientError(f"images must be one of {list(frontdoor.IMAGE_MODES)}")
    with _lock:
        data = _editable()
        c = _get(data, name)
        for key, value in (("tool_view", tool_view), ("images", images)):
            if value is not None:
                c[key] = value
        _save(data)
    _audit("tool view", name, tool_view=tool_view, images=images)


TOOLSET_MAX = 200


def set_toolset(name: str, patterns: list[str] | None) -> None:
    """The tools the front door shows this app, as grant patterns ("etsy.*", "read:image", an exact id), or None for
    its whole grant. It only narrows: what runs is still the grant. HomeShed Pro applies it (pro_frontdoor.py); the
    free HomeShed keeps it, so a set saved while Pro lapsed works again when Pro comes back."""
    if patterns is not None:
        if not isinstance(patterns, list) or len(patterns) > TOOLSET_MAX or not all(
                isinstance(p, str) and re.fullmatch(r"[A-Za-z0-9_.:*-]{1,80}", p) for p in patterns):
            raise ClientError(f"a tool set is a list of up to {TOOLSET_MAX} tool ids or patterns such as etsy.* or "
                              "read:image")
        patterns = sorted(set(patterns))
    with _lock:
        data = _editable()
        c = _get(data, name)
        if patterns is None:
            c.pop("toolset", None)
        else:
            c["toolset"] = patterns
        _save(data)
    _audit("tool set", name, patterns=patterns)


def toolset(name: str | None) -> list[str] | None:
    if name is None:
        return None
    ts = (_load()["clients"].get(name) or {}).get("toolset")
    return ts if isinstance(ts, list) else None


def in_toolset(patterns: list[str], tool_id: str, risk: str, category: str) -> bool:
    return any(_match(p, tool_id, risk, category) for p in patterns)


def set_preset(name: str, preset: str) -> None:
    if preset not in PRESETS:
        raise ClientError(f"preset must be one of {sorted(PRESETS)}")
    with _lock:
        data = _editable()
        c = _get(data, name)
        c["allow"], c["deny"] = list(PRESETS[preset]), []
        _save(data)
    _audit("access preset", name, preset=preset)


def grant(name: str, tool_id: str, risk: str, category: str) -> None:
    with _lock:
        data = _editable()
        c = _get(data, name)
        c["deny"] = [p for p in c["deny"] if p != tool_id]
        if not _allowed(c, tool_id, risk, category):
            c["allow"].append(tool_id)
        _save(data)
    _audit("granted", name, tool=tool_id)


def revoke(name: str, tool_id: str, risk: str, category: str) -> None:
    with _lock:
        data = _editable()
        c = _get(data, name)
        c["allow"] = [p for p in c["allow"] if p != tool_id]
        if _allowed(c, tool_id, risk, category):  # still allowed through a pattern: deny it explicitly
            c["deny"].append(tool_id)
        _save(data)
    _audit("revoked", name, tool=tool_id)


def _spent(name: str) -> dict | None:
    try:
        import spend
        return spend.spent(name)
    except Exception:  # noqa: BLE001 - the list still shows without it
        return None


def list_clients() -> dict:
    """Every client without its token hash, plus whether the file is readable."""
    data, now = _load(), time.time()
    out = []
    for name, c in sorted(data["clients"].items()):
        out.append({
            "name": name, "note": c.get("note", ""), "created": c.get("created"), "token_created": c.get("token_created"),
            "expires": c.get("expires"), "expired": bool(c.get("expires") and c["expires"] < now),
            "suspended": bool(c.get("suspended")), "allow": c.get("allow", []), "deny": c.get("deny", []),
            "rate_per_min": c.get("rate_per_min"), "last_used": max(filter(None, [c.get("last_used"), _last_seen.get(name)]), default=None),
            "memory_agent": c.get("memory_agent"), "memory_shared_read": c.get("memory_shared_read") is not False,
            "daily_spend_usd": c.get("daily_spend_usd"), "spend_today": _spent(name),
            "cap_delegated": bool(c.get("cap_delegated")), "control_keys": c.get("control_keys"),
            "managed_by": c.get("managed_by"), "tool_view": c.get("tool_view") or "full",
            "images": c.get("images") or "inline", "toolset": c.get("toolset"),
        })
    return {"clients": out, "unreadable": bool(data.get("unreadable"))}


def audit_trail(limit: int = 50) -> list[dict]:
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# --- runtime checks (called on every client request / tool call) --------------------------------

def resolve(authorization: str | None) -> tuple[str | None, str]:
    """(client name, status) for an Authorization header. Status: ok, unknown, suspended, expired, unreadable."""
    data = _load()
    if data.get("unreadable"):
        return None, "unreadable"
    if not authorization or not authorization.startswith("Bearer "):
        return None, "unknown"
    if authorization.startswith("Bearer hso_"):  # an OAuth sign-in's access token (oauth_server.py): same app checks
        import oauth_server
        name = oauth_server.resolve(authorization[7:])
        c = data["clients"].get(name) if name else None
        if not c:
            return None, "unknown"
        if c.get("suspended"):
            return name, "suspended"
        if c.get("expires") and c["expires"] < time.time():
            return name, "expired"
        return name, "ok"
    h = _hash(authorization[7:])
    for name, c in data["clients"].items():
        if hmac.compare_digest(h, c.get("token_sha256", "")):
            if c.get("suspended"):
                return name, "suspended"
            if c.get("expires") and c["expires"] < time.time():
                return name, "expired"
            return name, "ok"
    return None, "unknown"


def _match(pattern: str, tool_id: str, risk: str, category: str) -> bool:
    if pattern == "*":
        return True
    if ":" in pattern:
        r, cat = pattern.split(":", 1)
        return r == risk and cat in ("*", category)
    if pattern.endswith(".*"):
        return tool_id.startswith(pattern[:-1])
    return pattern == tool_id


FRONT_DOOR = frozenset({"tools.find", "tools.describe", "tools.call", "tools.groups", "tools.result"})
# Every app may use these without a grant (an explicit deny still wins): the front door only shows and runs what the
# rest of the grant allows, and the approvals tools only touch the app's own requests (approvals.decide also needs
# the owner to have named the app in "Apps whose own approval counts").
ALWAYS = FRONT_DOOR | {"approvals.status", "approvals.card", "approvals.decide"}


def _allowed(c: dict, tool_id: str, risk: str, category: str) -> bool:
    if tool_id in ALWAYS:
        return tool_id not in c.get("deny", [])
    return (any(_match(p, tool_id, risk, category) for p in c.get("allow", []))
            and not any(_match(p, tool_id, risk, category) for p in c.get("deny", [])))


def is_allowed(name: str, tool_id: str, risk: str, category: str) -> bool:
    c = _load()["clients"].get(name)
    return bool(c) and _allowed(c, tool_id, risk, category)


def check_call(name: str, tool_id: str, risk: str, category: str) -> tuple[bool, str]:
    """Grant + rate limit for one tool call by a client. Returns (ok, reason shown to the client)."""
    c = _load()["clients"].get(name)
    if not c:
        return False, f"client {name!r} no longer exists"
    if not _allowed(c, tool_id, risk, category):
        return False, (f"{tool_id} is not granted to client {name!r}. "
                       "The owner can grant it to this client.")
    limit = c.get("rate_per_min")
    now = time.time()
    with _lock:
        window = _windows.setdefault(name, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if limit and len(window) >= limit:
            return False, f"client {name!r} is over its limit of {limit} calls per minute; try again shortly"
        window.append(now)
    touch(name)
    return True, ""


def connection_tag(caller: str | None, host: str | None) -> str | None:
    """Short, stable tag for one caller on one machine (None when the source address is unknown)."""
    return hashlib.sha256(f"{caller or 'owner'}|{host}".encode()).hexdigest()[:6] if host else None


def seen(caller: str | None, host: str | None, call: bool) -> str | None:
    """Note activity on a connection and return its tag: listing tools marks it connected, a tool
    call also counts a call. caller None is the owner token."""
    tag = connection_tag(caller, host)
    if not tag:
        return None
    now = time.time()
    with _connections_lock:
        c = _connections.setdefault(tag, {"caller": caller or "owner", "host": host, "first": now, "calls": 0})
        c["last"] = now
        if call:
            c["calls"] += 1
        if len(_connections) > CONNECTION_PRUNE_AT:
            for old in [k for k, v in _connections.items() if now - v["last"] > CONNECTION_FORGET_S]:
                del _connections[old]
    return tag


def active_connections(window_s: int = CONNECTION_WINDOW_S) -> list[dict]:
    """Connections active within window_s, newest first: {tag, caller, host, first, last, calls}."""
    now = time.time()
    with _connections_lock:
        rows = [{"tag": k, **v} for k, v in _connections.items() if now - v["last"] <= window_s]
    return sorted(rows, key=lambda r: -r["last"])


def touch(name: str) -> None:
    """Record use; persisted at most once a minute per client (the file isn't rewritten per call)."""
    now = time.time()
    _last_seen[name] = now
    if now - _touched.get(name, 0) < TOUCH_EVERY_S:
        return
    _touched[name] = now
    try:
        with _lock:
            data = _editable()
            if name in data["clients"]:
                data["clients"][name]["last_used"] = now
                _save(data)
    except (ClientError, OSError):
        pass
