"""One place talks to the Proxmox VE API for the proxmox.* tools. See ../../capabilities/proxmox/read.md.

Credentials by name only (2026-10-03). One API token covers the whole Proxmox cluster; one node is the API entry
point, and every node in the cluster is reachable through it. The token is either one combined entry,
PROXMOX_API_TOKEN (or PROXMOX_TOKEN_ID) = user@realm!tokenname=secret (an optional PVEAPIToken= prefix is dropped),
or PROXMOX_TOKEN_ID = user@realm!tokenname plus PROXMOX_TOKEN_SECRET (the secret, when set, wins). Looked up with vault.secret() on every call (the Control Panel's Credentials first, then the
environment). It goes into one request header and nowhere else: never returned, logged or put in an error message.
Errors name the setting and the expected format, never any part of a value.

Settings (all optional except the token):
  PROXMOX_HOST        https://<address>:8006, no path. Required (an install may set a default privately).
  PROXMOX_CA_PATH     a CA file to trust for that host. A fresh Proxmox install has a self-signed certificate: copy
                      the node's /etc/pve/pve-root-ca.pem here and point this at it. TLS checking is never switched off.
Older names an install already uses can be mapped onto these in its own private settings (vault.secret).

Changes (start/stop/shutdown/reboot, snapshot, create) never run from a tool call: the tools validate and queue them
in proxmox_pending.py, and execute() below runs one only when the owner approves it. Nothing here deletes anything.
"""
from __future__ import annotations

import os
import re
import ssl
from datetime import UTC, datetime
from typing import Any

import httpx

try:  # the owner's own address lives in private_settings.py, which the public copy leaves out
    from private_settings import PROXMOX_DEFAULT_HOST as DEFAULT_HOST
except ImportError:
    DEFAULT_HOST = ""
try:  # an install's own stopgap for a host whose CA file isn't copied yet; the public copy has none
    from private_settings import PROXMOX_UNCHECKED_TLS_HOSTS as UNCHECKED_HOSTS
except ImportError:
    UNCHECKED_HOSTS = frozenset()
TIMEOUT_S = 10.0
TIMEOUT = httpx.Timeout(TIMEOUT_S, connect=5.0)
KINDS = ("qemu", "lxc")
POWER_ACTIONS = ("start", "stop", "shutdown", "reboot")
NODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$")
SNAP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,39}$")
HOST_RE = re.compile(r"^https://[A-Za-z0-9.\-\[\]:]+$")
OPTION_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
# Create options that could overwrite or destroy something, restore over a guest, or carry a secret: refused.
FORBIDDEN_OPTIONS = frozenset({"force", "delete", "purge", "destroy-unreferenced-disks", "archive", "restore",
                               "unique", "vmid", "node", "password", "cipassword"})
SECRET_WORDS = ("password", "secret", "token")
ALLOWED_SECRET_LOOKALIKES: frozenset[str] = frozenset()  # room for a harmless name that matches a word above
MAX_OPTIONS, MAX_OPTION_LEN = 60, 500

# Tests set this to an httpx.MockTransport so no request ever leaves the process.
_TRANSPORT: httpx.BaseTransport | None = None


class ProxmoxError(RuntimeError):
    """A missing or bad setting, an invalid argument, or Proxmox not answering cleanly. Never holds a credential."""


# --- settings ---------------------------------------------------------------------------------------------------

def _setting(name: str, default: str | None = None) -> str | None:
    import vault  # stored credentials first, then .env (vault.py)
    value = vault.secret(name)
    return value.strip() if isinstance(value, str) and value.strip() else default


def config() -> dict:
    """{"base": host, "verify": bool | SSLContext}. Holds no credential."""
    host = (_setting("PROXMOX_HOST") or DEFAULT_HOST or "").rstrip("/")
    if not host:
        raise ProxmoxError(f"PROXMOX_HOST is not set: add it (https://<address>:8006) under the Control Panel's "
                           "Credentials or the tool server's .env")
    if not HOST_RE.match(host):
        raise ProxmoxError(f"PROXMOX_HOST must look like https://<address>:8006 (https, no path)")
    verify: bool | ssl.SSLContext = host not in UNCHECKED_HOSTS
    ca = _setting("PROXMOX_CA_PATH")
    if ca:
        if not os.path.isfile(ca):
            raise ProxmoxError(f"PROXMOX_CA_PATH names a file that doesn't exist here: {ca}")
        verify = ssl.create_default_context(cafile=ca)
    return {"base": host, "verify": verify}


TOKEN_ID_RE = re.compile(r"^[^\s@!=:]+@[A-Za-z0-9._-]+![A-Za-z][A-Za-z0-9._-]{0,63}$")  # user@realm!tokenname
TOKEN_SECRET_RE = re.compile(r"^[^\s=]{8,256}$")
TOKEN_FORMAT = "user@realm!tokenname=secret (optionally starting PVEAPIToken=)"


def _named(name: str) -> str | None:
    import vault
    value = vault.secret(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strip_prefix(raw: str) -> str:
    return raw[len("PVEAPIToken="):] if raw[:len("PVEAPIToken=")].lower() == "pveapitoken=" else raw


def token() -> tuple[str, str]:
    """(token id, secret). Two shapes are accepted (the owner keeps the whole token in one entry):
    - PROXMOX_TOKEN_ID (user@realm!tokenname) plus PROXMOX_TOKEN_SECRET; the secret, when set, wins.
    - one combined token, user@realm!tokenname=secret (an optional PVEAPIToken= prefix is dropped), in
      PROXMOX_TOKEN_ID, or else in the general PROXMOX_API_TOKEN.
    Errors name the setting and the expected format, never any part of a value."""
    id_name, secret_name, alias = "PROXMOX_TOKEN_ID", "PROXMOX_TOKEN_SECRET", "PROXMOX_API_TOKEN"
    raw_id, secret = _named(id_name), _named(secret_name)
    if secret:
        if not raw_id:
            raise ProxmoxError(f"{secret_name} is set but {id_name} isn't: add {id_name} (user@realm!tokenname) "
                               "under the Control Panel's Credentials")
        token_id = _strip_prefix(raw_id)
        if "=" in token_id:
            raise ProxmoxError(f"{id_name} holds a whole token ({TOKEN_FORMAT}) while {secret_name} is also set: "
                               f"keep one of them (store just user@realm!tokenname in {id_name}, or remove "
                               f"{secret_name}). Values aren't shown")
        name = id_name
        secret_source = secret_name
    else:
        name = id_name if raw_id else alias
        raw = raw_id or _named(alias)
        if not raw:
            raise ProxmoxError(f"no Proxmox API token is set: add {id_name} under the Control Panel's Credentials "
                               f"(or the tool server's .env) as {TOKEN_FORMAT}, or {id_name} plus {secret_name}. "
                               "Only the names are needed here; values are never shown")
        combined = _strip_prefix(raw)
        bang = combined.find("!")
        eq = combined.find("=", bang + 1) if bang >= 0 else -1
        if eq < 0:
            raise ProxmoxError(f"{name} should hold the whole token as {TOKEN_FORMAT}, or set {secret_name} as "
                               "well; the stored value doesn't match (it isn't shown)")
        token_id, secret = combined[:eq], combined[eq + 1:]
        secret_source = name
    if not TOKEN_ID_RE.match(token_id):
        raise ProxmoxError(f"the token id in {name} should look like user@realm!tokenname (for example "
                           "root@pam!grokbot); the stored value doesn't (it isn't shown)")
    if not TOKEN_SECRET_RE.match(secret):
        raise ProxmoxError(f"the token secret in {secret_source} "
                           "should be the UUID Proxmox showed when the token was made (no spaces, no '='); the "
                           "stored value doesn't match (it isn't shown)")
    return token_id, secret


def _auth_header() -> dict:
    token_id, secret = token()
    return {"Authorization": f"PVEAPIToken={token_id}={secret}"}


# --- requests ---------------------------------------------------------------------------------------------------

def request(method: str, path: str, params: dict | None = None, data: dict | None = None) -> Any:
    """One Proxmox API call; returns its "data". Every failure is a ProxmoxError with a plain reason."""
    cfg = config()
    headers = _auth_header()
    url = f"{cfg['base']}/api2/json{path}"
    kwargs: dict = {"verify": cfg["verify"], "timeout": TIMEOUT, "follow_redirects": False}
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    try:
        with httpx.Client(**kwargs) as http:
            resp = http.request(method, url, params=params, data=data, headers=headers)
    except httpx.TimeoutException:
        raise ProxmoxError(f"Proxmox at {cfg['base']} didn't answer within {TIMEOUT_S:.0f} seconds") from None
    except httpx.ConnectError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "certificate verify failed" in str(exc):
            raise ProxmoxError(f"the TLS certificate at {cfg['base']} isn't trusted: copy the node's "
                               f"/etc/pve/pve-root-ca.pem here and set PROXMOX_CA_PATH to it") from None
        raise ProxmoxError(f"could not reach Proxmox at {cfg['base']}: {type(exc).__name__}") from None
    except httpx.RequestError as exc:
        raise ProxmoxError(f"could not reach Proxmox at {cfg['base']}: {type(exc).__name__}") from None
    if resp.status_code == 401:
        raise ProxmoxError(f"Proxmox refused the API token (HTTP 401): check PROXMOX_TOKEN_ID and "
                           f"PROXMOX_TOKEN_SECRET, and that the token hasn't expired")
    if resp.status_code == 403:
        raise ProxmoxError(f"the API token may not do this (HTTP 403 on {method} {path}): give its role the "
                           "privilege needed (VM.Audit/Sys.Audit to read, VM.PowerMgmt, VM.Snapshot, VM.Allocate)")
    if not 200 <= resp.status_code < 300:
        reason = (resp.reason_phrase or "").strip()[:200]
        detail = ""
        try:
            errors = resp.json().get("errors")
            if isinstance(errors, dict):
                detail = "; " + "; ".join(f"{k}: {str(v)[:120]}" for k, v in list(errors.items())[:5])
        except (ValueError, AttributeError):
            pass
        raise ProxmoxError(f"Proxmox answered HTTP {resp.status_code} to {method} {path}: {reason}{detail}")
    try:
        return resp.json().get("data")
    except (ValueError, AttributeError):
        raise ProxmoxError(f"unexpected (non-JSON) answer from Proxmox to {method} {path}") from None


def get(path: str, params: dict | None = None) -> Any:
    return request("GET", path, params=params)


# --- validation -------------------------------------------------------------------------------------------------

def check_node(node: str) -> str:
    node = str(node or "").strip()
    if not NODE_RE.match(node):
        raise ProxmoxError("node must be a Proxmox node name such as 'pve' (letters, digits, '.', '-')")
    return node


def check_vmid(vmid) -> int:
    if isinstance(vmid, bool) or not isinstance(vmid, int) or not 100 <= vmid <= 999_999_999:
        raise ProxmoxError("vmid must be a whole number from 100 to 999999999")
    return vmid


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ProxmoxError(f"kind must be one of {list(KINDS)} (qemu = virtual machine, lxc = container)")
    return kind


def check_action(action: str) -> str:
    if action not in POWER_ACTIONS:
        raise ProxmoxError(f"action must be one of {list(POWER_ACTIONS)}")
    return action


def check_snapname(name: str) -> str:
    if not SNAP_RE.match(str(name or "")):
        raise ProxmoxError("snapname must start with a letter, then letters, digits, '_' or '-', 2-40 characters")
    return name


def check_options(kind: str, options) -> dict:
    """Create options: flat {key: str|int|float|bool}, nothing that overwrites, destroys, restores or holds a secret."""
    if not isinstance(options, dict):
        raise ProxmoxError("options must be an object of Proxmox create parameters, e.g. {\"name\": \"web\", "
                           "\"memory\": 2048}")
    if len(options) > MAX_OPTIONS:
        raise ProxmoxError(f"at most {MAX_OPTIONS} options")
    out: dict = {}
    for key, value in options.items():
        if not isinstance(key, str) or not OPTION_KEY_RE.match(key):
            raise ProxmoxError(f"option name {str(key)[:40]!r} isn't a Proxmox parameter name")
        if key in FORBIDDEN_OPTIONS or (key not in ALLOWED_SECRET_LOOKALIKES and any(w in key for w in SECRET_WORDS)):
            raise ProxmoxError(f"option {key!r} isn't allowed here (it can overwrite, destroy or restore, or holds a "
                               "secret); set it in the Proxmox UI instead")
        if isinstance(value, bool):
            value = 1 if value else 0
        if not isinstance(value, (str, int, float)):
            raise ProxmoxError(f"option {key!r} must be text, a number or true/false")
        if len(str(value)) > MAX_OPTION_LEN or any(c in str(value) for c in "\r\n"):
            raise ProxmoxError(f"option {key!r} is too long or has a line break")
        out[key] = value
    if kind == "lxc" and not out.get("ostemplate"):
        raise ProxmoxError("an lxc container needs options.ostemplate, e.g. local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst")
    return out


def check_action_record(action: dict) -> dict:
    """Re-validate a queued change before it runs (the queue file is data, not trusted code)."""
    if not isinstance(action, dict):
        raise ProxmoxError("not a Proxmox change")
    op = action.get("op")
    rec = {"op": op, "node": check_node(action.get("node")), "kind": check_kind(action.get("kind")),
           "vmid": check_vmid(action.get("vmid"))}
    if op == "power":
        rec["action"] = check_action(action.get("action"))
    elif op == "snapshot":
        rec["snapname"] = check_snapname(action.get("snapname"))
        rec["description"] = str(action.get("description") or "")[:200]
    elif op == "create":
        rec["options"] = check_options(rec["kind"], action.get("options"))
    else:
        raise ProxmoxError("op must be power, snapshot or create")
    return rec


# --- helpers shared by the tools ----------------------------------------------------------------------------------

def gib(n) -> float | None:
    return round(n / 1024 ** 3, 2) if isinstance(n, (int, float)) else None


def pct(n) -> float | None:
    return round(n * 100, 1) if isinstance(n, (int, float)) else None


def iso(ts) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds") if isinstance(ts, (int, float)) else None


def guests(node: str = "") -> list[dict]:
    """Every VM and container, from /cluster/resources (one call), optionally on one node."""
    rows = get("/cluster/resources", {"type": "vm"}) or []
    return [r for r in rows if isinstance(r, dict) and (not node or r.get("node") == node)]


def find_guest(vmid: int) -> dict | None:
    return next((r for r in guests() if r.get("vmid") == vmid), None)


def resolve_node(vmid: int, node: str = "") -> str:
    """The node a guest is on: the one given (checked by name), else looked up cluster-wide by vmid (vmids are unique
    across a Proxmox cluster), so callers needn't know where a guest lives."""
    if node:
        return check_node(node)
    found = find_guest(check_vmid(vmid))
    if not found or not found.get("node"):
        raise ProxmoxError(f"no VM or container with vmid {vmid} on this cluster")
    return found["node"]


def current_status(node: str, kind: str, vmid: int) -> dict:
    return get(f"/nodes/{node}/{kind}/{vmid}/status/current") or {}


def preflight(rec: dict) -> dict:
    """Read-only checks for a change, run by the dry run, when it's queued, and again just before it runs."""
    node, kind, vmid = rec["node"], rec["kind"], rec["vmid"]
    nodes = {n.get("node") for n in (get("/nodes") or []) if isinstance(n, dict)}
    if node not in nodes:
        raise ProxmoxError(f"no node called {node!r} (nodes: {sorted(x for x in nodes if x)})")
    found = find_guest(vmid)
    if rec["op"] == "create":
        if found:
            raise ProxmoxError(f"vmid {vmid} is already used by {found.get('type')} {found.get('name')!r} on "
                               f"{found.get('node')}: pick a free one (create never replaces a guest)")
        return {"vmid_free": True}
    if not found:
        raise ProxmoxError(f"no {kind} with vmid {vmid} on this cluster")
    if found.get("node") != node or found.get("type") != kind:
        raise ProxmoxError(f"vmid {vmid} is a {found.get('type')} on {found.get('node')}, not a {kind} on {node}")
    status = current_status(node, kind, vmid)
    out = {"name": status.get("name") or found.get("name"), "status_now": status.get("status"),
           "lock": status.get("lock")}
    if status.get("lock"):
        raise ProxmoxError(f"{kind} {vmid} is locked ({status.get('lock')}): wait for that to finish")
    if rec["op"] == "power":
        a, s = rec["action"], status.get("status")
        if a == "start" and s == "running":
            out["note"] = "already running: start would do nothing"
        elif a in ("stop", "shutdown", "reboot") and s == "stopped":
            out["note"] = f"already stopped: {a} would do nothing"
        if a == "stop":
            out["warning"] = "stop is a hard power-off (like pulling the plug); shutdown asks the guest OS politely"
    return out


def describe(rec: dict) -> str:
    where = f"{rec['kind']} {rec['vmid']} on {rec['node']}"
    if rec["op"] == "power":
        return f"{rec['action']} {where}"
    if rec["op"] == "snapshot":
        return f"snapshot {where} as {rec['snapname']!r}"
    return f"create {where} with {len(rec['options'])} option(s)"


def execute(action: dict) -> dict:
    """Run one approved change. Only proxmox_pending.decide() calls this. Returns Proxmox's task id (UPID)."""
    rec = check_action_record(action)
    checks = preflight(rec)
    node, kind, vmid = rec["node"], rec["kind"], rec["vmid"]
    if rec["op"] == "power":
        upid = request("POST", f"/nodes/{node}/{kind}/{vmid}/status/{rec['action']}")
    elif rec["op"] == "snapshot":
        data = {"snapname": rec["snapname"]}
        if rec["description"]:
            data["description"] = rec["description"]
        upid = request("POST", f"/nodes/{node}/{kind}/{vmid}/snapshot", data=data)
    else:
        upid = request("POST", f"/nodes/{node}/{kind}", data={"vmid": vmid, **rec["options"]})
    return {"done": True, "change": describe(rec), "upid": upid, "checks": checks,
            "next": f"proxmox.tasks.list(node={node!r}) shows how the task went"}
