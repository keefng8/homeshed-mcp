"""HomeShed Control Panel: the web page for a HomeShed install (FastAPI). It shows what's running and what it has
saved, and lets the owner change settings, keys and who may look, with view-only logins for anyone else.

Holds every upstream credential server-side: the browser never sees a HomeShed or memory-core token, only this
panel's own login.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

import httpx
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MCP_BASE_URL = os.environ["MCP_BASE_URL"]
MCP_AUTH_TOKEN = os.environ["MCP_AUTH_TOKEN"]
MCP_HOST_HEADER = os.environ["MCP_HOST_HEADER"]

# The shared memory service (memory-core) is optional (release gate B, 2026-10-01): with no address its parts of the
# panel say "Not set up yet", never "Down".
MEMORY_CORE_BASE_URL = os.environ.get("MEMORY_CORE_BASE_URL", "").strip().rstrip("/")
MEMORY_CORE_BEARER = os.environ.get("MEMORY_CORE_BEARER", "")
MEMORY_SERVICE_ID = os.environ.get("MEMORY_SERVICE_ID", "")
MEMORY_USER_KEY = os.environ.get("MEMORY_USER_KEY", "")
MEMORY_TEAM_ID = os.environ.get("MEMORY_TEAM_ID", "")
MEMORY_USER_ID = os.environ.get("MEMORY_USER_ID", "")
MEMORY_AGENT_ID = os.environ.get("MEMORY_AGENT_ID", "")
NOT_SET_UP = "not-set-up"  # a part with no address: grey "Not set up yet" on the page, never red
# No memory-core address: memory is HomeShed's own store, read through the tool server (the prepper's clean-VM
# test, 2026-10-01: a new install's built-in memory looked missing).
BUILT_IN = "built-in"

# The owner's password: set here (the .env), or left empty. Empty, the panel makes one on its first start, keeps only a
# salted hash in OWNER_FILE and prints the password once to its log. There is no default password and no open mode
# (release gate C7, 2026-10-01: before, an empty password left every page open to anyone who reached the port).
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
# View-only logins live in VIEWERS_FILE, managed on the Settings page. This older single viewer
# password, if set, is moved into that list once (as "friend") and can then be removed from .env.
DASHBOARD_VIEWER_PASSWORD = os.environ.get("DASHBOARD_VIEWER_PASSWORD", "")
# The address friends use (the public one); login messages fall back to the address in use.
DASHBOARD_PUBLIC_URL = os.environ.get("DASHBOARD_PUBLIC_URL", "")

# Known facts categories — mirrors mcp-server/capabilities/memory/remember_fact.md's table.
# No "list all categories" API exists on memory-core, so this list is maintained here, same as
# it's maintained in that doc. Keep both in sync when a category is added/retired.
FACT_CATEGORIES = [
    {"id": "general", "label": "General", "legacy": True},
    {"id": "platform", "label": "Platform"},
    {"id": "mcp-server", "label": "Tool server"},
    {"id": "memory-stack", "label": "Memory system"},
    {"id": "local-ai", "label": "Local AI"},
    {"id": "known-gaps", "label": "Known gaps"},
    {"id": "conventions", "label": "Conventions", "legacy": True},
    {"id": "external-audits", "label": "External audits"},
    {"id": "bugs-fixed", "label": "Bugs fixed"},
    {"id": "tasks", "label": "Tasks"},
    {"id": "scripts", "label": "Scripts"},
]

# Headers for mcp-server's REST routes (RequestGuard checks Host and the bearer token).
_MCP_HEADERS = {"Host": MCP_HOST_HEADER, "Authorization": f"Bearer {MCP_AUTH_TOKEN}"}
# ntfy's /v1/health answers {"healthy": true}. Empty: phone alerts aren't set up (no personal default: release gate A).
NTFY_HEALTH_URL = os.environ.get("NTFY_HEALTH_URL", "").strip()

# Model servers the panel polls for tokens and status. The owner's own list, with his machines' addresses, lives in
# private_panel.py, which the public copy leaves out. Without it the panel lists the models HomeShed itself is
# configured with (the tool server's /local-ai/backends), with status but no token counts.
try:
    from private_panel import local_ai_servers as _owner_servers
    LOCAL_AI_SERVERS = _owner_servers(MCP_BASE_URL, _MCP_HEADERS)
except ImportError:
    LOCAL_AI_SERVERS = []
LOCAL_AI_HISTORY_LEN = 120  # ~10 min at a 5s poll interval
_local_ai_history: dict[str, list[dict]] = {s["name"]: [] for s in LOCAL_AI_SERVERS}
_METRIC_RE = r"(?m)^{name}(?:\{{[^\r\n]*\}})?\s+([0-9]+(?:\.[0-9]+)?)\s*$"
_LABELED_METRIC_RE = r'(?m)^{name}\{{([^\r\n]*)\}}\s+([0-9]+(?:\.[0-9]+)?)\s*$'

app = FastAPI(title="HomeShed Control Panel")
# Owner-only parts (private_routes.py, which the public copy leaves out) add their own answers here: start-up
# tasks, Overview feature checks, installed parts, Models page readings and the page's own settings. Empty
# without that file.
ADD_ONS: dict[str, list] = {"startup": [], "features": [], "installed": [], "models": [], "ui": [], "packs": [],
                            "pack_install": []}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

# Real login page + signed session cookie (2026-09-25, the owner: "a real login rather than a dialog
# box ... match the theme"). Replaces the HTTP Basic popup:
#   - GET /login serves static/login.html; POST /login checks the password (constant time) and
#     sets an HMAC-signed, HttpOnly, SameSite=Lax cookie (Secure over HTTPS). 7 days, or 30
#     with "remember me".
#   - Brute-force guard: 5 failures per client IP -> 5-minute lockout (429).
#   - Browser page loads without a session redirect to /login?next=...; API calls get a plain 401
#     WITHOUT WWW-Authenticate, so the browser's password dialog never appears again.
#   - Basic auth is still accepted silently, so scripts (the deploy health check, curl -u) keep
#     working.
# --- The Control Panel's own settings (Settings page > Control Panel, Sharing, API access) -----------
# the owner 2026-09-28: "finish the full settings panel ... many options to change ... in categories".
# Kept in /data/settings.json on the dashboard's volume, re-read only when it changes; a missing or
# unreadable file, or a saved value that no longer fits, means the default.
# Where the panel keeps its own files: /data in its container; `homeshed-mcp panel` points it at the server's data
# folder instead. Each file can still be moved on its own.
PANEL_DATA_DIR = Path(os.environ.get("PANEL_DATA_DIR") or "/data")  # empty (as copied from .env.example): the default
PANEL_SETTINGS_FILE = Path(os.environ.get("PANEL_SETTINGS_FILE") or PANEL_DATA_DIR / "settings.json")
URL_RE = re.compile(r"^https?://[^\s/]+[^\s]*$")
PANEL_SCHEMA = {
    # 2026-09-29 (the owner: "these findings are important to improve. do it."): the line under the title was one setup's
    # folder path written into the page. Each install names its own now.
    "panel_name": {"type": "text", "default": "", "max": 40, "group": "Control Panel",
                   "label": "Name under the title",
                   "help": "Shown under Control Panel at the top of the menu, so you can tell your setups apart.",
                   "pattern": r"^[A-Za-z0-9][A-Za-z0-9' .&()_-]{0,39}$",
                   "rule": "letters, numbers, spaces and simple punctuation"},
    "refresh_seconds": {"type": "int", "min": 2, "max": 60, "default": 4, "group": "Control Panel",
                        "label": "Update the page every (seconds)",
                        "help": "How often the Control Panel fetches fresh numbers. A higher number uses less power."},
    "animations": {"type": "bool", "default": True, "group": "Control Panel",
                   "label": "Moving pictures",
                   "help": "The animated core on the Overview, and everything else that moves. "
                           "Switch off on slow devices."},
    "session_days": {"type": "int", "min": 1, "max": 90, "default": 7, "group": "Control Panel",
                     "label": "Stay signed in for (days)",
                     "help": "How long a login lasts before the password is asked for again. "
                             "Ticking 'remember me' keeps it for 30 days."},
    "client_default_rate": {"type": "int", "min": 1, "max": 100000, "default": 120, "group": "API access",
                            "label": "Calls per minute for a new client",
                            "help": "The limit filled in when you add an app or project on the API access page. "
                                    "You can still change it for each client."},
    "public_url": {"type": "url", "default": DASHBOARD_PUBLIC_URL, "group": "Sharing",
                   "label": "Public address of this Control Panel",
                   "help": "The web address people outside your home network use. It's sent with new view-only logins."},
    "viewers_see_savings": {"type": "bool", "default": True, "group": "Sharing",
                            "label": "View-only logins can see Savings",
                            "help": "The token savings charts. The list of missed commands is never shown to them."},
    "guide_library_url": {"type": "url", "default": os.environ.get("GUIDE_LIBRARY_URL", ""), "group": "Sharing",
                          "label": "Guide library address",
                          "help": "Where the Guide page reads shared guides from: the central library, e.g. "
                                  "https://…/api/guides. Leave empty to show the guides that come with this install."},
    "viewers_see_context": {"type": "bool", "default": True, "group": "Sharing",
                            "label": "View-only logins can see Claude's context",
                            "help": "How full each Claude session is, without folder names."},
}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z' .-]{0,39}$")
_panel_cache: dict = {"sig": None, "data": {}}


def _panel_validate(key: str, value):
    if key not in PANEL_SCHEMA:
        raise ValueError(f"Unknown setting: {key}")
    spec = PANEL_SCHEMA[key]
    if spec["type"] == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not spec["min"] <= value <= spec["max"]:
            raise ValueError(f"{spec['label']}: use a whole number from {spec['min']} to {spec['max']}.")
    elif spec["type"] == "bool" and not isinstance(value, bool):
        raise ValueError(f"{spec['label']}: must be on or off.")
    elif spec["type"] == "url":
        value = value.strip() if isinstance(value, str) else value
        if not (isinstance(value, str) and (value == "" or URL_RE.match(value))):
            raise ValueError(f"{spec['label']}: a web address starting with https:// (or http://), or empty.")
    elif spec["type"] == "select" and value not in spec["options"]:
        raise ValueError(f"{spec['label']}: choose one of the listed options.")
    elif spec["type"] == "text":
        value = value.strip() if isinstance(value, str) else value
        rx = re.compile(spec["pattern"]) if spec.get("pattern") else NAME_RE
        if not (isinstance(value, str) and (value == "" or rx.match(value))):
            rule = spec.get("rule", "letters, spaces, apostrophes and dashes")
            raise ValueError(f"{spec['label']}: {rule}, up to {spec['max']}.")
    return value


def _panel_raw() -> dict:
    try:
        st = PANEL_SETTINGS_FILE.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        _panel_cache.update(sig=None, data={})
        return {}
    if sig != _panel_cache["sig"]:
        try:
            data = json.loads(PANEL_SETTINGS_FILE.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        _panel_cache.update(sig=sig, data=data)
    return dict(_panel_cache["data"])


def panel_value(key: str):
    raw = _panel_raw()
    try:
        return _panel_validate(key, raw[key]) if key in raw else PANEL_SCHEMA[key]["default"]
    except ValueError:
        return PANEL_SCHEMA[key]["default"]


def panel_values() -> dict:
    return {key: panel_value(key) for key in PANEL_SCHEMA}


def panel_update(changes: dict) -> dict:
    checked = {key: _panel_validate(key, value) for key, value in changes.items()}
    raw = _panel_raw()
    raw.update(checked)
    PANEL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PANEL_SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=1), encoding="utf-8")
    tmp.replace(PANEL_SETTINGS_FILE)
    _panel_cache["sig"] = None
    return panel_values()


SESSION_COOKIE = "cp_session"
SESSION_DAYS, REMEMBER_DAYS = 7, 30
MAX_FAILURES, LOCKOUT_S = 5, 300
GLOBAL_MAX_FAILURES = 50  # wrong passwords from every address together in LOCKOUT_S: a spread-out guesser stops too
_PUBLIC_PATHS = {"/login", "/login.html", "/logout", "/favicon.ico", "/healthz", "/manifest.webmanifest"}
# The self-hosted heading font, the app icons and the background art (static/media/bg): the sign-in page uses them too,
# a browser fetches the manifest (and the icons it names) without cookies, and nothing private is in any of them.
_PUBLIC_PREFIXES = ("/fonts/", "/icons/", "/media/bg/")
_failures: dict[str, list[float]] = {}
_all_failures: list[float] = []
# cf-connecting-ip and x-forwarded-for can be sent by anyone who reaches the port directly, so they're believed only
# when the panel sits behind a proxy that sets them (for example Cloudflare, then Caddy). Otherwise the connection's own.
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in ("1", "true", "yes")
OWNER_FILE = Path(os.environ.get("OWNER_FILE") or PANEL_DATA_DIR / "owner.json")
REVOKED_FILE = Path(os.environ.get("REVOKED_SESSIONS_FILE") or PANEL_DATA_DIR / "revoked-sessions.json")
# A random secret made on first run (owner-only file), mixed into the key that signs the owner's cookie when the password
# comes from the .env: a key made from the password alone let a stolen cookie test password guesses offline, with no
# lockout (R&D's security review, 2026-10-01). A new password still signs everyone out.
SESSION_SECRET_FILE = Path(os.environ.get("SESSION_SECRET_FILE") or PANEL_DATA_DIR / "session-secret")
_server_secret_cache: dict = {"value": None}
MIN_OWNER_PASSWORD = 12
# OWASP's current PBKDF2-HMAC-SHA256 figure (R&D's note, 2026-10-01): the owner chooses this password, so it gets the
# full count, stored beside the hash so a later rise can re-hash. View-only passwords are random and made here (59
# bits), and signing in checks each of them, so they keep PBKDF2_ROUNDS.
OWNER_ROUNDS = 600_000
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_owner_cache: dict = {"key": None, "data": None}
_revoked_cache: dict = {"key": None, "data": {}}
_log = logging.getLogger("dashboard")


def _file_key(path: Path):
    """(path, mtime, size) to re-read a small state file only when it changed; None when it's missing."""
    st = path.stat()
    return str(path), st.st_mtime_ns, st.st_size


def _load_owner() -> dict | None:
    """The saved owner password: {"salt", "hash", "session_secret", "set_at"}; None when there's no file. An unreadable
    file gives {"unreadable": True}: then no owner password works and nothing is saved over it (fail closed)."""
    try:
        key = _file_key(OWNER_FILE)
    except FileNotFoundError:
        return None
    except OSError:
        return {"unreadable": True}
    if key != _owner_cache["key"]:
        try:
            data = json.loads(OWNER_FILE.read_text(encoding="utf-8"))
            if not all(isinstance(data.get(k), str) and data[k] for k in ("salt", "hash", "session_secret")):
                raise ValueError("bad shape")
        except (OSError, ValueError, AttributeError):
            data = {"unreadable": True}
        _owner_cache.update(key=key, data=data)
    return _owner_cache["data"]


def _save_owner(password: str) -> None:
    """A new salt, hash and session secret: saving a password also signs every session out."""
    salt = secrets.token_hex(16)
    OWNER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OWNER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"salt": salt, "hash": _hash_pw(password, salt, OWNER_ROUNDS), "rounds": OWNER_ROUNDS,
                               "session_secret": secrets.token_hex(32), "set_at": time.time()}), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(OWNER_FILE)
    _owner_cache["key"] = None


def first_run_password() -> str | None:
    """No password in the .env and none saved: make one, keep only its hash, and return it for the log, once.
    To start again, delete OWNER_FILE and restart the panel."""
    if DASHBOARD_PASSWORD or _load_owner() is not None:
        return None
    password = _new_password(groups=4)
    _save_owner(password)
    return password


def _owner_ok(password: str) -> bool:
    if not password:
        return False
    if DASHBOARD_PASSWORD:
        return secrets.compare_digest(password.encode(), DASHBOARD_PASSWORD.encode())
    owner = _load_owner()
    if not owner or owner.get("unreadable"):
        return False
    rounds = owner.get("rounds") if isinstance(owner.get("rounds"), int) else PBKDF2_ROUNDS
    return secrets.compare_digest(_hash_pw(password, owner["salt"], rounds), owner["hash"])


def _server_secret() -> str:
    """SESSION_SECRET_FILE's random secret, made on first run (created exclusively, readable by the owner only). If it
    can't be saved, one for this run: sessions then last until the next restart."""
    if _server_secret_cache["value"]:
        return _server_secret_cache["value"]
    try:
        value = SESSION_SECRET_FILE.read_text(encoding="ascii").strip()
    except OSError:
        value = ""
    if len(value) < 32:
        value = secrets.token_hex(32)
        try:
            SESSION_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(SESSION_SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(value)
        except FileExistsError:  # another start made it first: use that one
            value = SESSION_SECRET_FILE.read_text(encoding="ascii").strip() or value
        except OSError:
            pass
    _server_secret_cache["value"] = value
    return value


def _session_key() -> bytes:
    # An explicit secret if set; otherwise the .env password with this panel's own random secret (a cookie alone can't
    # test passwords), or the saved owner's own random secret, so a new password signs everyone out. No owner password
    # anywhere: a random key, which no cookie can match.
    owner = None if DASHBOARD_PASSWORD else _load_owner()
    secret = (os.environ.get("DASHBOARD_SESSION_SECRET")
              or (f"session:{_server_secret()}:{DASHBOARD_PASSWORD}" if DASHBOARD_PASSWORD else "")
              or str((owner or {}).get("session_secret") or ""))
    return hashlib.sha256(secret.encode()).digest() if secret else secrets.token_bytes(32)


def _revoked() -> dict[str, float]:
    """Signed-out sessions: sha256 of the cookie -> its expiry. Kept on disk, so a restart doesn't bring one back."""
    try:
        key = _file_key(REVOKED_FILE)
    except OSError:
        return {}
    if key != _revoked_cache["key"]:
        try:
            data = {str(k): float(v) for k, v in json.loads(REVOKED_FILE.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, TypeError, AttributeError):
            data = {}
        _revoked_cache.update(key=key, data=data)
    return _revoked_cache["data"]


def _revoke(token: str) -> None:
    now = time.time()
    expiry = token.split(".", 1)[0]
    data = {k: v for k, v in _revoked().items() if v > now}  # expired entries fall out
    data[hashlib.sha256(token.encode()).hexdigest()] = float(expiry) if expiry.isdigit() else now + REMEMBER_DAYS * 86400
    REVOKED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = REVOKED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(REVOKED_FILE)
    _revoked_cache["key"] = None


def _same_origin(request: Request) -> bool:
    """Did this browser request come from the panel's own pages? Its Origin (or Referer) host must be the host it was
    sent to, as the panel or the proxy in front of it sees it, or the public address in Settings. A cross-site page
    can't set these headers, and "null" (a sandboxed frame) never matches."""
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    host = urlsplit(source).netloc.lower() if source and source != "null" else ""
    allowed = {request.headers.get("host", "").lower(), request.headers.get("x-forwarded-host", "").lower()}
    for url in (panel_value("public_url"), DASHBOARD_PUBLIC_URL):
        if url:
            allowed.add(urlsplit(url).netloc.lower())
    return bool(host) and host in allowed - {""}


# --- View-only logins (the owner, 2026-09-28: "give the owner the ability to set viewers credentials in
# settings. so i can allow more than one"). Each has a name, a note, a salted PBKDF2 hash (never the
# password), an optional expiry and the last sign-in. A viewer's cookie is signed with a key made from
# that viewer's own salt, so a new password or removal signs them out at once. An unreadable file
# fails closed: no viewer can sign in and nothing is saved over it; the owner is unaffected.
VIEWERS_FILE = Path(os.environ.get("VIEWERS_FILE") or PANEL_DATA_DIR / "viewers.json")
VIEWER_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
MAX_VIEWERS = 25
PBKDF2_ROUNDS = 100_000
PW_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: easy to read out or type on a phone
_viewers_cache: dict = {"sig": None, "data": None}


def _hash_pw(password: str, salt: str, rounds: int = PBKDF2_ROUNDS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), rounds).hex()


def _new_password(groups: int = 3) -> str:
    return "-".join("".join(secrets.choice(PW_ALPHABET) for _ in range(4)) for _ in range(groups))


def _save_viewers(data: dict) -> None:
    VIEWERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VIEWERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v for k, v in data.items() if k != "unreadable"}, indent=1), encoding="utf-8")
    tmp.replace(VIEWERS_FILE)
    _viewers_cache["sig"] = None


def _load_viewers() -> dict:
    """{"viewers": {name: record}}, re-read only when the file changes."""
    try:
        st = VIEWERS_FILE.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    if sig != _viewers_cache["sig"] or _viewers_cache["data"] is None:
        data = {"viewers": {}}
        if sig:
            try:
                loaded = json.loads(VIEWERS_FILE.read_text(encoding="utf-8"))
                if not (isinstance(loaded, dict) and isinstance(loaded.get("viewers"), dict)):
                    raise ValueError("bad shape")
                data = loaded
            except (OSError, ValueError):
                data = {"viewers": {}, "unreadable": True}
        _viewers_cache.update(sig=sig, data=data)
    data = _viewers_cache["data"]
    if DASHBOARD_VIEWER_PASSWORD and not data.get("env_moved") and not data.get("unreadable"):
        salt = secrets.token_hex(16)
        data["viewers"].setdefault("friend", {"note": "moved from .env", "salt": salt,
                                              "hash": _hash_pw(DASHBOARD_VIEWER_PASSWORD, salt),
                                              "created": time.time(), "expires": None})
        data["env_moved"] = True
        _save_viewers(data)
        data = _load_viewers()
    return data


def _live_viewer(name: str) -> dict | None:
    v = _load_viewers()["viewers"].get(name)
    return v if v and not (v.get("expires") and v["expires"] < time.time()) else None


def _viewer_for_password(password: str) -> str | None:
    for name in list(_load_viewers()["viewers"]):
        v = _live_viewer(name)
        if v and secrets.compare_digest(_hash_pw(password, v["salt"]), v["hash"]):
            return name
    return None


def _viewer_key(name: str, salt: str) -> bytes:
    return hashlib.sha256(f"viewer-session:{name}:{salt}".encode()).digest()


def _make_session(days: int, role: str = "owner", name: str = "") -> str:
    expiry = str(int(time.time()) + days * 86400)
    if role == "viewer":
        v = _live_viewer(name) or {"salt": secrets.token_hex(16)}  # unknown: a cookie nothing accepts
        sig = hmac.new(_viewer_key(name, v["salt"]), f"{expiry}.v.{name}".encode(), hashlib.sha256).hexdigest()
        return f"{expiry}.v.{name}.{sig}"
    sig = hmac.new(_session_key(), expiry.encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def _session_identity(token: str | None) -> tuple[str | None, str | None]:
    """(role, viewer name): ("owner", None), ("viewer", name) or (None, None). The owner's cookie keeps
    its original expiry.sig form; a viewer's is expiry.v.name.sig and can never pass as the owner's."""
    if not token:
        return None, None
    parts = token.split(".")
    if len(parts) == 2:
        expiry, sig = parts
        good = hmac.new(_session_key(), expiry.encode(), hashlib.sha256).hexdigest()
        role, name = "owner", None
    elif len(parts) == 4 and parts[1] == "v":
        expiry, _, name, sig = parts
        v = _live_viewer(name)
        if not v:
            return None, None
        good = hmac.new(_viewer_key(name, v["salt"]), f"{expiry}.v.{name}".encode(), hashlib.sha256).hexdigest()
        role = "viewer"
    else:
        return None, None
    if (secrets.compare_digest(sig, good) and expiry.isdigit() and int(expiry) > time.time()
            and hashlib.sha256(token.encode()).hexdigest() not in _revoked()):  # signed out: over, on every restart too
        return role, name
    return None, None


def _session_role(token: str | None) -> str | None:
    return _session_identity(token)[0]


def _valid_session(token: str | None) -> bool:
    return _session_role(token) is not None


# Viewer login (the owner, 2026-09-28: "two users for my frontend control panel ... give my friend a look
# ... i dont want him to see sensitive things", then "dont allow to read memory. the text from the
# memory i mean"). A viewer can GET only the routes below; each can be scrubbed of private fields
# first. Every other route and method is refused by the middleware (403), so hiding pages in the
# browser is cosmetic, not the protection. Not shared: memory text, the agent's tasks and files,
# clients and connections, containers, uptime targets, images, observations, settings, models.
def _viewer_memory(d: dict) -> dict:
    d = _keep(d, VIEWER_FIELDS["/api/memory"]) or {}
    for c in d.get("categories") or []:
        c["latest"] = []  # counts only, never what the memories say (the page still expects the list)
    return d


# Fields a viewer may see, by allow-list (2026-09-29, from the Research and Development session's review): the feeds
# behind these routes grow, and removing named fields let new ones through (session settings and project folders,
# API client names, each call's client). Now anything not named here is dropped, the same rule the routes follow.
# A spec is a dict of keys (each with its own spec; "*" means any key), a one-item list for each item of a list, or
# True for a plain value (a dict or list there is dropped too).
def _keep(value, spec):
    if spec is True:
        return None if isinstance(value, (dict, list)) else value
    if isinstance(spec, list):
        return [_keep(v, spec[0]) for v in value] if isinstance(value, list) else None
    if not isinstance(spec, dict) or not isinstance(value, dict):
        return None
    if "*" in spec:
        return {k: _keep(v, spec["*"]) for k, v in value.items()}
    return {k: _keep(value[k], s) for k, s in spec.items() if k in value}


def _plain(*keys: str) -> dict:
    return {k: True for k in keys}


_RTK_TOTALS = _plain("available", "error", "total_commands", "total_input", "total_output", "total_saved",
                     "avg_savings_pct", "total_time_ms", "avg_time_ms")
_USAGE = {**_plain("since", "estimated_tokens_saved", "images_generated", "calls", "chars_per_token"),
          "by_rule": {"*": True}, "rules": {"*": True},
          "capabilities": {"*": _plain("calls", "errors", "in_chars", "out_chars", "saved_chars", "rule", "last",
                                       "saved_tokens")}}
_MODEL_ROW = _plain("name", "online", "prompt_tokens", "output_tokens")
_LOCAL_AI_ROW = {**_plain("name", "label", "type", "online", "requests_processing", "requests_deferred", "requests_total",
                          "prompt_tokens", "output_tokens", "total_tokens", "delta_tokens_window", "window_seconds",
                          "avg_ms"), "sparkline": [True]}
VIEWER_FIELDS = {
    # R8's remainder (2026-09-29): counts, never the text; each tool's public description, never its handler, inputs
    # or aliases.
    "/api/memory": {"categories": [_plain("id", "label", "legacy", "count", "count_capped")]},
    "/api/capabilities": [_plain("id", "name", "category", "risk", "description")],
    "/api/local-ai": {"servers": [_LOCAL_AI_ROW]},
    "/api/features": {"features": [_plain("label", "status", "detail")]},
    "/api/context": {"sessions": [{**_plain("used_pct", "alarm_pct", "project", "model", "updated", "window",
                                            "tokens_in", "tokens_out"), "compactions": [_plain("pct", "at")]}],
                     "settings": _plain("context_alarm_pct"), "live_window_s": True},
    "/api/tokens": {"rtk": _RTK_TOTALS, "estimate": _USAGE, "local_models": [_MODEL_ROW], "cloud_models": [_MODEL_ROW]},
    "/api/savings": {"daily": {**_plain("available", "error"), "summary": _RTK_TOTALS,
                               "daily": [_plain("date", "commands", "input_tokens", "output_tokens", "saved_tokens",
                                                "savings_pct", "total_time_ms", "avg_time_ms")]},
                     "estimate": _USAGE},  # not "discover": the missed-savings scan lists (redacted) command lines
    "/api/activity": [_plain("id", "timestamp", "duration_ms", "ok")],
}


def _viewer_fields(path: str):
    return lambda d: _keep(d, VIEWER_FIELDS[path])


def _viewer_context(d: dict) -> dict:
    d = _keep(d, VIEWER_FIELDS["/api/context"])
    for i, s in enumerate(d.get("sessions") or [], 1):
        s["project"] = f"Session {i}"  # which projects the owner works on isn't the viewer's business
    return d


VIEWER_ROUTES = {
    "/": None, "/index.html": None, "/api/me": None,
    "/api/health": None, "/api/capabilities": _viewer_fields("/api/capabilities"),
    "/api/activity": _viewer_fields("/api/activity"), "/api/tokens": _viewer_fields("/api/tokens"),
    "/api/local-ai": _viewer_fields("/api/local-ai"),
    "/api/features": _viewer_fields("/api/features"),
    "/api/memory": _viewer_memory, "/api/context": _viewer_context, "/api/savings": _viewer_fields("/api/savings"),
    "/api/ui-settings": None, "/api/guides": None, "/api/installed": None,
    "/favicon.ico": None, "/manifest.webmanifest": None,
}
VIEWER_PREFIXES = ("/api/guides/", "/fonts/", "/icons/", "/media/bg/")  # a guide; the heading font; icons; backgrounds
# Routes the owner can hide from view-only logins on the Settings page (Sharing). /api/tokens carries the same
# numbers as Savings, so the same switch covers it (it could be read around the switch before).
VIEWER_SWITCHES = {"/api/savings": "viewers_see_savings", "/api/tokens": "viewers_see_savings",
                   "/api/context": "viewers_see_context"}


async def _viewer_request(request: Request, call_next):
    path = request.url.path
    if (request.method not in ("GET", "HEAD")
            or (path not in VIEWER_ROUTES and not path.startswith(VIEWER_PREFIXES))
            or (path in VIEWER_SWITCHES and not panel_value(VIEWER_SWITCHES[path]))):
        return JSONResponse({"detail": "Not available with a view-only login."}, status_code=403)
    response = await call_next(request)
    if response.status_code >= 400 and path.startswith("/api/"):
        # An error's own words can name internal addresses ("mcp-server unreachable: ..."): viewers get the status only
        # (the Research and Development session's re-check, 2026-09-29).
        return JSONResponse({"detail": "Not available right now."}, status_code=response.status_code)
    scrub = VIEWER_ROUTES.get(path)
    if scrub is None or response.status_code != 200:
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    try:
        return JSONResponse(scrub(json.loads(body)))
    except (ValueError, AttributeError):
        return JSONResponse({"detail": "unexpected reply"}, status_code=502)


def _basic_ok(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        _, pw = base64.b64decode(auth[6:]).decode().split(":", 1)
    except Exception:
        return False
    return _owner_ok(pw)


# Who may say where a request really came from (R&D's security review, 2026-10-01: with TRUST_PROXY_HEADERS on, anyone
# who could reach the panel could claim any address in cf-connecting-ip and step round the per-address lockout).
# TRUSTED_PROXIES (addresses or ranges, comma-separated) if set; else this machine and the container's own gateway,
# where a tunnel on the same host arrives through the published port (checked on the owner's server, 2026-10-02: the
# panel's live traffic came from the gateway, a LAN client from its own address).
TRUSTED_PROXIES = os.environ.get("TRUSTED_PROXIES", "")
_trusted_nets: list = []


def _gateway() -> str | None:
    """The container's default gateway (Linux, /proc/net/route), or None."""
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 2 and fields[1] == "00000000":
                import socket
                import struct
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except (OSError, ValueError):
        return None
    return None


def _proxy_trusted(peer: str) -> bool:
    import ipaddress
    if not _trusted_nets:
        given = [p.strip() for p in TRUSTED_PROXIES.split(",") if p.strip()]
        for p in given or ["127.0.0.1", "::1", *([_gateway()] if _gateway() else [])]:
            try:
                _trusted_nets.append(ipaddress.ip_network(p, strict=False))
            except ValueError:
                _log.warning("TRUSTED_PROXIES: %r isn't an address or range, ignored", p)
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(ip in net for net in _trusted_nets if net.version == ip.version)


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "?"
    if TRUST_PROXY_HEADERS and _proxy_trusted(peer):
        forwarded = (request.headers.get("cf-connecting-ip")
                     or request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        if forwarded:
            return forwarded
    return peer


@app.middleware("http")
async def session_auth(request: Request, call_next):
    # No "." or ".." segments and no "\" (browsers resolve them before sending): the prefix allow-lists here and for
    # view-only logins would pass "/fonts/../index.html" while the static files serve /index.html (found in review,
    # 2026-09-30; under Windows they split on "\" too). Refusing them first means every check sees what the router sees.
    path = request.url.path
    if "\\" in path or any(part in (".", "..") for part in path.split("/")):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)
    role, viewer = _session_identity(request.cookies.get(SESSION_COOKIE))
    by_cookie = role is not None
    if role is None and request.headers.get("authorization", "").startswith("Basic "):
        # Basic auth (scripts) counts against the sign-in page's lockout: it never recorded a failure, so it allowed
        # unlimited guesses (R&D's security review, 2026-10-01, confirmed high).
        ip, now = _client_ip(request), time.time()
        if (blocked := _login_blocked(ip, now)) is not None:
            return blocked
        if _basic_ok(request):
            role = "owner"
        else:
            _login_failed(ip, now, "password (Basic auth)")
    request.state.role, request.state.viewer = role, viewer
    if by_cookie and request.method in _UNSAFE_METHODS and not _same_origin(request):
        # CSRF (gate C9): the browser sends the cookie with a forged cross-site request too, so a change must come from
        # the panel's own page. Scripts that sign in with Basic auth carry no cookie and aren't affected.
        return JSONResponse({"detail": "This change didn't come from the Control Panel's own page, so it was refused."},
                            status_code=403)
    if role == "owner":
        return await call_next(request)
    if role == "viewer":
        return await _viewer_request(request, call_next)
    if request.url.path.startswith("/api/") or "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    from urllib.parse import quote
    from fastapi.responses import RedirectResponse

    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='/')}", status_code=302)


@app.get("/login")
async def login_page():
    from fastapi.responses import FileResponse

    return FileResponse(Path(__file__).parent / "static" / "login.html", media_type="text/html")


def _login_blocked(ip: str, now: float) -> JSONResponse | None:
    """The lockout: 5 wrong passwords from one address, or 50 from all together, in 5 minutes."""
    recent = _failures[ip] = [t for t in _failures.get(ip, []) if now - t < LOCKOUT_S]
    _all_failures[:] = [t for t in _all_failures if now - t < LOCKOUT_S]
    if len(recent) >= MAX_FAILURES:
        wait = int(LOCKOUT_S - (now - recent[0]))
        return JSONResponse({"detail": f"Too many attempts. Try again in {max(wait, 1)}s."}, status_code=429)
    if len(_all_failures) >= GLOBAL_MAX_FAILURES:
        return JSONResponse({"detail": "Too many wrong passwords from everywhere. Try again in a few minutes."},
                            status_code=429)
    return None


def _login_failed(ip: str, now: float, what: str) -> int:
    """Counts a wrong password and logs it (never the password): returns how many tries this address has left."""
    _failures.setdefault(ip, []).append(now)
    _all_failures.append(now)
    _log.warning("Control Panel: wrong %s from %s (%d in the last 5 minutes)", what, ip, len(_failures[ip]))
    return MAX_FAILURES - len(_failures[ip])


@app.post("/login")
async def login(request: Request):
    if "origin" in request.headers and not _same_origin(request):
        # Login CSRF: another site can't sign this browser in to an account of its choosing. No Origin: a script.
        return JSONResponse({"detail": "Sign in from the Control Panel's own page."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    # Check, verify and record with no await between them: a parallel burst of guesses can't all pass the lockout
    # check before the first failure is counted (R&D's security review, 2026-10-01: it was checked before the await).
    ip, now = _client_ip(request), time.time()
    if (blocked := _login_blocked(ip, now)) is not None:
        return blocked
    body = body if isinstance(body, dict) else {}
    password = str(body.get("password") or "")
    role, name = None, None
    if _owner_ok(password):
        role = "owner"
    elif password:
        name = _viewer_for_password(password)
        role = "viewer" if name else None
    if role is None:
        left = _login_failed(ip, now, "password")
        return JSONResponse({"detail": "Wrong password" + (f" · {left} tries left" if left > 0 else " · locked for 5 minutes")},
                            status_code=401)
    _failures.pop(ip, None)
    days = REMEMBER_DAYS if body.get("remember") else panel_value("session_days")
    https = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
    if role == "viewer":
        data = _load_viewers()
        data["viewers"][name]["last_login"] = time.time()
        _save_viewers(data)
    resp = JSONResponse({"ok": True, "role": role})
    resp.set_cookie(SESSION_COOKIE, _make_session(days, role, name or ""), max_age=days * 86400, httponly=True,
                    samesite="lax", secure=https, path="/")
    return resp


@app.get("/healthz")
async def healthz():
    """Liveness for Docker's HEALTHCHECK and uptime monitors: needs no login and says only that the panel is up (with
    no .env password, a first-run install has no password the HEALTHCHECK could send)."""
    return {"status": "ok"}


@app.get("/api/me")
async def me(request: Request):
    """Which login this is, so the page can hide what a viewer can't open anyway."""
    return {"role": getattr(request.state, "role", None) or "owner", "name": getattr(request.state, "viewer", None)}


# --- managing view-only logins (owner only: these routes aren't in VIEWER_ROUTES) -------------------
def _viewer_row(name: str, v: dict, now: float) -> dict:
    return {"name": name, "note": v.get("note", ""), "created": v.get("created"), "expires": v.get("expires"),
            "expired": bool(v.get("expires") and v["expires"] < now), "last_login": v.get("last_login")}


def _viewers_error(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


_UNREADABLE = ("The view-only logins file can't be read, so nothing was changed and no view-only login works "
               "until it's fixed. Your own login is unaffected.")


async def _send_login_to_phone(request: Request, name: str, password: str) -> bool:
    link = panel_value("public_url") or DASHBOARD_PUBLIC_URL or str(request.base_url).rstrip("/")
    try:
        await _mcp_call_tool("notify.send", {
            "title": f"Control Panel: view-only login for {name}",
            "message": (f"Link: {link}\nPassword: {password}\n\nThey can look at the Overview and Savings. "
                        "Nothing can be changed, and memory text, files, clients, tokens and settings are never shown. "
                        "Remove it or give a new password on the Settings page."),
            "priority": "default", "tags": ["key"]})
        return True
    except RuntimeError:
        return False


@app.get("/api/viewers")
async def viewers_list():
    data, now = _load_viewers(), time.time()
    return {"viewers": [_viewer_row(n, v, now) for n, v in sorted(data["viewers"].items())],
            "unreadable": bool(data.get("unreadable"))}


@app.post("/api/viewers")
async def viewer_create(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return _viewers_error("Send the new login as JSON.")
    name = str(body.get("name") or "").strip().lower()
    if not VIEWER_NAME_RE.match(name):
        return _viewers_error("Name: 2 to 32 characters, lowercase letters, numbers and dashes, starting with a letter.")
    days = body.get("expires_days")
    if days not in (None, "", 0) and not (isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 365):
        return _viewers_error("Expires: a number of days from 1 to 365, or never.")
    data = _load_viewers()
    if data.get("unreadable"):
        return _viewers_error(_UNREADABLE, 503)
    if name in data["viewers"]:
        return _viewers_error(f"There's already a login called {name}.")
    if len(data["viewers"]) >= MAX_VIEWERS:
        return _viewers_error(f"You can have up to {MAX_VIEWERS} view-only logins. Remove one first.")
    now, password, salt = time.time(), _new_password(), secrets.token_hex(16)
    data["viewers"][name] = {"note": str(body.get("note") or "")[:120], "salt": salt, "hash": _hash_pw(password, salt),
                             "created": now, "expires": now + days * 86400 if days else None}
    _save_viewers(data)
    sent = await _send_login_to_phone(request, name, password) if body.get("send_to_phone") else None
    return {"name": name, "password": password, "sent": sent}


@app.post("/api/viewers/{name}/reset")
async def viewer_reset(name: str, request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    data = _load_viewers()
    if data.get("unreadable"):
        return _viewers_error(_UNREADABLE, 503)
    v = data["viewers"].get(name)
    if not v:
        return _viewers_error(f"No login called {name}.", 404)
    password, salt = _new_password(), secrets.token_hex(16)
    v.update(salt=salt, hash=_hash_pw(password, salt))  # a new salt also signs them out
    _save_viewers(data)
    sent = await _send_login_to_phone(request, name, password) if (body or {}).get("send_to_phone") else None
    return {"name": name, "password": password, "sent": sent}


@app.delete("/api/viewers/{name}")
async def viewer_remove(name: str):
    data = _load_viewers()
    if data.get("unreadable"):
        return _viewers_error(_UNREADABLE, 503)
    if data["viewers"].pop(name, None) is None:
        return _viewers_error(f"No login called {name}.", 404)
    _save_viewers(data)
    return {"removed": name}


@app.get("/logout")
async def logout(request: Request):
    """Signing out ends the session on the server too: a copy of the cookie stops working (gate C8)."""
    from fastapi.responses import RedirectResponse

    token = request.cookies.get(SESSION_COOKIE)
    if token and _session_identity(token)[0] is not None:
        _revoke(token)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# --- the owner's own password (owner only: these routes aren't in VIEWER_ROUTES) ---------------------------------
@app.get("/api/owner")
async def owner_info():
    """How the owner's password is set, for Settings > Account and sharing: never the password or its hash."""
    owner = None if DASHBOARD_PASSWORD else _load_owner()
    return {"in_env": bool(DASHBOARD_PASSWORD), "set_at": (owner or {}).get("set_at"),
            "unreadable": bool((owner or {}).get("unreadable")), "min_length": MIN_OWNER_PASSWORD}


@app.post("/api/owner/password")
async def owner_password(request: Request):
    """{current, new}: a new owner password. Every other session ends; this browser stays signed in."""
    if DASHBOARD_PASSWORD:
        return JSONResponse({"error": "Your password is set in the panel's .env file (DASHBOARD_PASSWORD). Change it "
                                      "there, then restart the panel."}, status_code=409)
    ip, now = _client_ip(request), time.time()
    if (blocked := _login_blocked(ip, now)) is not None:
        return blocked
    body = await _json_body(request)
    current, new = str(body.get("current") or ""), str(body.get("new") or "")
    if not _owner_ok(current):
        _login_failed(ip, now, "current password")
        return JSONResponse({"error": "That isn't your current password."}, status_code=403)
    if len(new) < MIN_OWNER_PASSWORD or new == current:
        return JSONResponse({"error": f"Choose a new password of at least {MIN_OWNER_PASSWORD} characters, different "
                                      "from the current one."}, status_code=400)
    _save_owner(new)
    days = panel_value("session_days")
    https = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, _make_session(days, "owner"), max_age=days * 86400, httponly=True,
                    samesite="lax", secure=https, path="/")
    return resp


# ---------------------------------------------------------------------------
# Local-AI usage polling
# ---------------------------------------------------------------------------

def _parse_metric(text: str, names: list[str]) -> float | None:
    for name in names:
        m = re.search(_METRIC_RE.format(name=re.escape(name)), text)
        if m:
            return float(m.group(1))
    return None


def _parse_labeled_metric(text: str, metric_name: str, label_key: str, label_value: str) -> float | None:
    needle = f'{label_key}="{label_value}"'
    for m in re.finditer(_LABELED_METRIC_RE.format(name=re.escape(metric_name)), text):
        if needle in m.group(1):
            return float(m.group(2))
    return None


LIFETIME_FILE = Path(os.environ.get("LIFETIME_FILE") or PANEL_DATA_DIR / "local_ai_lifetime.json")


def _load_lifetime() -> dict:
    try:
        return json.loads(LIFETIME_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_lifetime: dict = _load_lifetime()


def _accumulate_lifetime(name: str, prompt: float | None, output: float | None) -> None:
    """Add this sample's growth to a lifetime total. A counter that went DOWN means the backend
    restarted (counters restart at 0), so its whole current value is new work."""
    entry = _lifetime.setdefault(name, {"prompt": 0.0, "output": 0.0, "last_prompt": None, "last_output": None})
    for key, value in (("prompt", prompt), ("output", output)):
        if value is None:
            continue
        last = entry[f"last_{key}"]
        entry[key] += value if last is None or value < last else value - last
        entry[f"last_{key}"] = value
    try:
        LIFETIME_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIFETIME_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_lifetime), encoding="utf-8")
        tmp.replace(LIFETIME_FILE)
    except OSError:
        pass  # totals still work in memory; persistence is best-effort


async def _poll_local_ai_once(client: httpx.AsyncClient) -> None:
    fetched: dict[str, httpx.Response | Exception] = {}  # one request per URL per cycle (the gateway serves both NVIDIA entries)
    for server in LOCAL_AI_SERVERS:
        sample: dict = {"timestamp": time.time(), "online": False}
        try:
            if server["url"] not in fetched:
                try:
                    fetched[server["url"]] = await client.get(server["url"], headers=server.get("headers"), timeout=3)
                except httpx.RequestError as exc:
                    fetched[server["url"]] = exc
            resp = fetched[server["url"]]
            if isinstance(resp, Exception):
                raise resp
            if resp.status_code == 200:
                text = resp.text
                sample["online"] = True
                sample["requests_processing"] = None
                sample["requests_deferred"] = None
                sample["requests_total"] = None
                if server["type"] == "decider":  # a decision server: counts decisions, not tokens
                    stats = resp.json()
                    sample["online"] = stats.get("status") == "ok"
                    sample["requests_total"] = stats.get("served")
                    sample["requests_processing"] = stats.get("busy")
                    sample["avg_ms"] = stats.get("avg_ms")
                    hist = _local_ai_history[server["name"]]
                    hist.append(sample)
                    del hist[: len(hist) - LOCAL_AI_HISTORY_LEN]
                    continue  # decisions, not tokens: nothing to add to the token totals
                if server["type"] == "gateway":
                    model = server["model_label"]
                    # Genuine cumulative counter (unlike llama.cpp, which has no such metric).
                    sample["requests_total"] = _parse_labeled_metric(
                        text, "litellm_deployment_total_requests_total", "litellm_model_name", model
                    )
                    prompt = _parse_labeled_metric(text, "litellm_input_tokens_metric_total", "model", model)
                    output = _parse_labeled_metric(text, "litellm_output_tokens_metric_total", "model", model)
                else:
                    sample["requests_processing"] = _parse_metric(text, ["llamacpp:requests_processing"])
                    sample["requests_deferred"] = _parse_metric(text, ["llamacpp:requests_deferred"])
                    prompt = _parse_metric(text, ["llamacpp:prompt_tokens_total", "llamacpp_prompt_tokens_total"])
                    output = _parse_metric(text, ["llamacpp:tokens_predicted_total", "llamacpp_tokens_predicted_total"])
                sample["prompt_tokens"] = prompt
                sample["output_tokens"] = output
                _accumulate_lifetime(server["name"], prompt, output)
                sample["total_tokens"] = (
                    (prompt or 0) + (output or 0) if (prompt is not None or output is not None) else None
                )
        except (httpx.RequestError, ValueError):
            pass
        hist = _local_ai_history[server["name"]]
        hist.append(sample)
        del hist[: len(hist) - LOCAL_AI_HISTORY_LEN]


async def _local_ai_poller() -> None:
    async with httpx.AsyncClient() as client:
        while True:
            await _poll_local_ai_once(client)
            await asyncio.sleep(5)


@app.on_event("startup")
async def _start_poller() -> None:
    made = first_run_password()
    if made:  # shown this once, where only whoever started the panel can see it (its terminal, or docker logs)
        print(f"HomeShed Control Panel, first start: sign in with this password: {made}\n"
              "It's shown only this once. Change it in Settings > Account and sharing.", flush=True)
    asyncio.create_task(_local_ai_poller())
    for start in ADD_ONS["startup"]:
        start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _facts_session_id(category: str) -> str:
    return "facts" if category == "general" else f"facts:{category}"


FACT_INDEX = "fact-index"  # the categories in use, as memory.remember_fact records them (tools/memory/recall_relevant.py)
_CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")  # the tool server's own rule for a category name
MAX_CATEGORIES = 40  # bounds the queries per poll; the known ones come first


def _with_found(index: list | None) -> list[dict]:
    """FACT_CATEGORIES, then any other category the store's fact index names (an install's own, such as "decisions":
    the prepper's clean-VM run counted 2 of 3 facts, 2026-10-01), oldest first, up to MAX_CATEGORIES."""
    known, extra = {c["id"] for c in FACT_CATEGORIES}, []
    for m in reversed(index or []):  # the index comes newest first
        name = str(m.get("content") or "").strip()
        if _CATEGORY_RE.match(name) and name != FACT_INDEX and name not in known and name not in extra:
            extra.append(name)
    return (FACT_CATEGORIES + [{"id": n, "label": n.replace("-", " ").capitalize()} for n in extra])[:MAX_CATEGORIES]


# ---------------------------------------------------------------------------
# Real MCP protocol client -- initialize -> tools/call, for capabilities that
# aren't exposed via mcp-server's own /capabilities or /activity REST routes.
# Minimal by design (one tool call per HTTP request, no session reuse across
# requests) -- this dashboard is low-traffic, and a fresh session per call is
# simpler and more robust than managing session lifetime across concurrent
# browser tabs. Add capabilities here as the dashboard actually needs them,
# not speculatively.
# ---------------------------------------------------------------------------

async def _mcp_call_tool(tool_name: str, arguments: dict, timeout: int = 15, quiet: bool = False) -> dict:
    """Real MCP Streamable HTTP handshake against mcp-server: initialize ->
    notifications/initialized -> tools/call. Raises RuntimeError with a clear
    message on any failure -- callers turn that into a JSONResponse, same
    "never leak raw exception internals" pattern mcp-server's own capabilities
    use.

    timeout defaults to 15s (every existing caller's needs); local_ai.decide passes a longer
    one since its first-ever call after a cold model cache is slow (real cold-start download,
    not a bug -- see mcp-server/call-tool.sh's own 15s->60s timeout bump for the same finding).

    quiet: the page drawing itself (a card's refresh), not something the owner did: the tool server serves it without
    logging it as activity or usage (read tools only; the owner, 2026-10-02: the Release progress card's 20 s refresh
    was 192 of the last 200 recent calls).
    """
    headers = {
        "Host": MCP_HOST_HEADER,
        "Authorization": f"Bearer {MCP_AUTH_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **({"X-Homelab-Quiet": "1"} if quiet else {}),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        init_resp = await client.post(
            f"{MCP_BASE_URL}/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "dashboard", "version": "1"},
                },
            },
        )
        if init_resp.status_code != 200:
            raise RuntimeError(f"mcp-server initialize returned http {init_resp.status_code}")
        session_id = init_resp.headers.get("mcp-session-id")
        if not session_id:
            raise RuntimeError("mcp-server initialize response had no mcp-session-id header")
        session_headers = {**headers, "mcp-session-id": session_id}

        await client.post(
            f"{MCP_BASE_URL}/mcp",
            headers=session_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        call_resp = await client.post(
            f"{MCP_BASE_URL}/mcp",
            headers=session_headers,
            json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
    if call_resp.status_code != 200:
        raise RuntimeError(f"mcp-server tools/call returned http {call_resp.status_code}")

    # Streamable HTTP responses may be plain JSON or a single-event SSE stream
    # (Content-Type: text/event-stream) -- parse both, don't assume one.
    content_type = call_resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        payload = None
        for line in call_resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[len("data:"):].strip())
                break
        if payload is None:
            raise RuntimeError("mcp-server tools/call SSE response had no data: line")
    else:
        payload = call_resp.json()

    result = payload.get("result")
    if result is None:
        error = payload.get("error", {})
        raise RuntimeError(error.get("message") or "mcp-server tools/call returned no result")
    if result.get("isError"):
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        raise RuntimeError(text or "capability raised an error")

    if "structuredContent" in result:
        return result["structuredContent"]

    # Fallback for SDK versions/tools that don't populate structuredContent. Most capabilities
    # (anything returning a single dict, e.g. uptime.status) emit exactly one text block --
    # parse and return it directly, same as before. A capability whose Python return type is a
    # top-level list (docker.container.list, found 2026-09-23 when this route started 500ing)
    # gets serialized as ONE TEXT BLOCK PER LIST ELEMENT instead -- blindly concatenating them,
    # as this used to do, produces invalid JSON ("Extra data") the moment the list has more than
    # one item. Parse each block on its own and, when there's more than one, wrap them the same
    # way the SDK's own structuredContent does for a bare list (`{"result": [...]}`) so callers
    # never need to know which path a given tool/SDK version took.
    blocks = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    if not blocks:
        raise RuntimeError("mcp-server tools/call returned no text content")
    parsed = [json.loads(b) for b in blocks]
    return parsed[0] if len(parsed) == 1 else {"result": parsed}


@app.get("/api/capabilities")
async def capabilities():
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"{MCP_BASE_URL}/capabilities",
                headers={"Host": MCP_HOST_HEADER, "Authorization": f"Bearer {MCP_AUTH_TOKEN}"},
            )
        except httpx.RequestError as exc:
            return JSONResponse({"error": f"mcp-server unreachable: {exc}"}, status_code=502)
    if resp.status_code != 200:
        return JSONResponse({"error": f"mcp-server returned {resp.status_code}"}, status_code=502)
    return resp.json()


# --- Every project's memory (the owner, 2026-09-28: "I don't see other projects memory either in the dash? Just this
# one"). Each project has its own agent in memory-core (the starter kit's bootstrap script makes it); the
# Memory panel used to read only this Control Panel's own. Choosing another project is owner-only: a view-only
# login always sees this project's counts, never the text.
MEMORY_QUERY_MAX = 100  # memory-core refuses a bigger query limit ("limit: Too big", found live 2026-09-28)
_agents_cache: dict = {"at": 0.0, "agents": None, "totals": {}, "totals_at": 0.0}


def _memory_headers() -> dict:
    return {"Authorization": f"Bearer {MEMORY_CORE_BEARER}", "x-tdai-service-id": MEMORY_SERVICE_ID,
            "x-tdai-user-key": MEMORY_USER_KEY}


async def _memory_agents(client) -> list[dict]:
    """[{id, name}] of every agent in the team, cached for a minute; just this project's own if the list fails."""
    if _agents_cache["agents"] is not None and time.time() - _agents_cache["at"] < 60:
        return _agents_cache["agents"]
    agents = [{"id": MEMORY_AGENT_ID, "name": "claude-code"}]
    try:
        r = await client.post(f"{MEMORY_CORE_BASE_URL}/v3/meta/agent/list", headers=_memory_headers(),
                              json={"team_id": MEMORY_TEAM_ID, "limit": 100})
        items = ((r.json().get("data") or {}).get("items") or []) if r.status_code == 200 else []
        found = [{"id": a["agent_id"], "name": a.get("name") or a["agent_id"]} for a in items if a.get("agent_id")]
        if found:
            agents = found
    except (httpx.RequestError, ValueError, AttributeError):
        pass
    _agents_cache.update(at=time.time(), agents=agents)
    return agents


async def _chosen_agent(request: Request, agent: str, client) -> str | None:
    """The agent to read: the owner's choice if it exists, this project's own otherwise (viewers always)."""
    if not agent or agent == MEMORY_AGENT_ID or getattr(request.state, "role", None) == "viewer":
        return MEMORY_AGENT_ID
    return agent if agent in {a["id"] for a in await _memory_agents(client)} else None


async def _fact_messages(client, agent_id: str, category: str, limit: int) -> list[dict] | None:
    """One fact category's messages for one agent; None when memory-core didn't answer properly."""
    try:
        resp = await client.post(f"{MEMORY_CORE_BASE_URL}/v3/conversation/query", headers=_memory_headers(), json={
            "team_id": MEMORY_TEAM_ID, "user_id": MEMORY_USER_ID, "agent_id": agent_id,
            "session_id": _facts_session_id(category), "limit": min(limit, MEMORY_QUERY_MAX)})
        return (resp.json().get("data") or {}).get("messages") or [] if resp.status_code == 200 else None
    except (httpx.RequestError, ValueError):
        return None


async def _store_facts(client: httpx.AsyncClient, category: str, limit: int, max_chars: int = 300) -> list | None:
    """One category's facts from HomeShed's own memory (the tool server's /memory/facts), newest first; None when it
    doesn't answer."""
    try:
        r = await client.get(f"{MCP_BASE_URL}/memory/facts", headers=_MCP_HEADERS,
                             params={"category": category, "limit": limit, "max_chars": max_chars})
        return (r.json().get("messages") or []) if r.status_code == 200 else None
    except (httpx.RequestError, ValueError, AttributeError):
        return None


async def _store_summary():
    """The Memory card from HomeShed's own memory: each category's count (up to 50) and its latest four."""
    async with httpx.AsyncClient(timeout=10) as client:
        cats = _with_found(await _store_facts(client, FACT_INDEX, 100, 60))
        got = await asyncio.gather(*(_store_facts(client, c["id"], 50) for c in cats))
    if all(m is None for m in got):
        return JSONResponse({"error": "HomeShed's memory didn't answer."}, status_code=502)
    return {"categories": [{"id": c["id"], "label": c["label"], "legacy": c.get("legacy", False),
                            "count": len(m or []), "count_capped": len(m or []) >= 50,
                            "latest": [{"content": x.get("content"), "timestamp": x.get("timestamp")} for x in (m or [])[:4]]}
                           for c, m in zip(cats, got)]}


@app.get("/api/memory/projects")
async def memory_projects():
    """Every project's memory agent and how many facts it holds (counts only), for the Memory panel's picker.
    Counting is 11 queries per agent, so totals are kept for two minutes. HomeShed's own memory has no picker."""
    if not MEMORY_CORE_BASE_URL:
        return {"projects": []}
    async with httpx.AsyncClient(timeout=10) as client:
        agents = await _memory_agents(client)
        if time.time() - _agents_cache["totals_at"] > 120:
            totals = {}
            for a in agents:
                cats = _with_found(await _fact_messages(client, a["id"], FACT_INDEX, MEMORY_QUERY_MAX))
                counts = await asyncio.gather(*(_fact_messages(client, a["id"], c["id"], MEMORY_QUERY_MAX)
                                                for c in cats))
                totals[a["id"]] = sum(len(m or []) for m in counts)
            _agents_cache.update(totals=totals, totals_at=time.time())
    rows = [{"id": a["id"], "name": a["name"], "mine": a["id"] == MEMORY_AGENT_ID,
             "facts": _agents_cache["totals"].get(a["id"], 0)} for a in agents]
    return {"projects": sorted(rows, key=lambda r: (not r["mine"], -r["facts"], r["name"]))}


@app.get("/api/memory")
async def memory_summary(request: Request, agent: str = ""):
    if not MEMORY_CORE_BASE_URL:
        return await _store_summary()
    results = []
    async with httpx.AsyncClient(timeout=10) as client:
        agent_id = await _chosen_agent(request, agent, client)
        if agent_id is None:
            return JSONResponse({"error": "No such project memory."}, status_code=404)
        for cat in _with_found(await _fact_messages(client, agent_id, FACT_INDEX, MEMORY_QUERY_MAX)):
            try:
                resp = await client.post(
                    f"{MEMORY_CORE_BASE_URL}/v3/conversation/query",
                    headers={
                        "Authorization": f"Bearer {MEMORY_CORE_BEARER}",
                        "x-tdai-service-id": MEMORY_SERVICE_ID,
                        "x-tdai-user-key": MEMORY_USER_KEY,
                    },
                    json={
                        "team_id": MEMORY_TEAM_ID,
                        "user_id": MEMORY_USER_ID,
                        "agent_id": agent_id,
                        "session_id": _facts_session_id(cat["id"]),
                        # Real bug found live 2026-09-24 (the owner: "I only see 4 of 8"): this used
                        # to query with limit=8, which capped not just the "latest" preview but
                        # `count` itself -- a category with 30 real facts reported "8" as its
                        # count, since len(messages) could never exceed the query's own limit.
                        # 50 makes `count` accurate for realistically-sized categories without
                        # querying every category at the full-list route's max (200) on every
                        # 4s poll. `latest` below still only shows 4 -- this is a multi-category
                        # overview, not the place to render everything; /api/memory/<id> (used
                        # when a category is expanded) is.
                        "limit": 50,
                    },
                )
                data = resp.json() if resp.status_code == 200 else {}
                messages = (data.get("data") or {}).get("messages") or []
            except httpx.RequestError:
                messages = []
            results.append(
                {
                    "id": cat["id"],
                    "label": cat["label"],
                    "legacy": cat.get("legacy", False),
                    "count": len(messages),
                    "count_capped": len(messages) >= 50,
                    "latest": [
                        {"content": m.get("content"), "timestamp": m.get("timestamp")}
                        for m in messages[:4]
                    ],
                }
            )
    return {"categories": results}


@app.get("/api/memory/{category}")
async def memory_category(category: str, request: Request, limit: int = 50, agent: str = ""):
    """Full fact list for one category -- /api/memory's own "latest" is deliberately capped to
    4 per category (it's a multi-category overview polled every 4s, not meant to carry every
    fact for every category at once). This is what the frontend calls when a category is
    expanded, so "click to see more" actually shows more instead of the same capped 4 -- real
    gap found live 2026-09-24 (the owner: "I only see 4 of 8")."""
    if not _CATEGORY_RE.match(category) or category == FACT_INDEX:  # any category in use, not only the listed ones
        return JSONResponse({"error": f"unknown category {category!r}"}, status_code=404)
    limit = max(1, min(limit, MEMORY_QUERY_MAX))
    if not MEMORY_CORE_BASE_URL:
        async with httpx.AsyncClient(timeout=10) as client:
            facts = await _store_facts(client, category, limit, 0)
        if facts is None:
            return JSONResponse({"error": "HomeShed's memory didn't answer."}, status_code=502)
        return {"id": category, "count": len(facts),
                "facts": [{"content": m.get("content"), "timestamp": m.get("timestamp")} for m in facts]}
    async with httpx.AsyncClient(timeout=10) as client:
        agent_id = await _chosen_agent(request, agent, client)
        if agent_id is None:
            return JSONResponse({"error": "No such project memory."}, status_code=404)
        try:
            resp = await client.post(
                f"{MEMORY_CORE_BASE_URL}/v3/conversation/query",
                headers={
                    "Authorization": f"Bearer {MEMORY_CORE_BEARER}",
                    "x-tdai-service-id": MEMORY_SERVICE_ID,
                    "x-tdai-user-key": MEMORY_USER_KEY,
                },
                json={
                    "team_id": MEMORY_TEAM_ID,
                    "user_id": MEMORY_USER_ID,
                    "agent_id": agent_id,
                    "session_id": _facts_session_id(category),
                    "limit": limit,
                },
            )
        except httpx.RequestError as exc:
            return JSONResponse({"error": f"memory-core unreachable: {exc}"}, status_code=502)
    if resp.status_code != 200:
        return JSONResponse({"error": f"memory-core returned {resp.status_code}"}, status_code=502)
    data = resp.json()
    messages = (data.get("data") or {}).get("messages") or []
    return {
        "id": category,
        "count": len(messages),
        "facts": [{"content": m.get("content"), "timestamp": m.get("timestamp")} for m in messages],
    }


@app.get("/api/activity")
async def activity():
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"{MCP_BASE_URL}/activity",
                headers={"Host": MCP_HOST_HEADER, "Authorization": f"Bearer {MCP_AUTH_TOKEN}"},
            )
        except httpx.RequestError as exc:
            return JSONResponse({"error": f"mcp-server unreachable: {exc}"}, status_code=502)
    if resp.status_code != 200:
        return JSONResponse({"error": f"mcp-server returned {resp.status_code}"}, status_code=502)
    return resp.json()


_backends_cache: dict = {"at": 0.0, "rows": []}


async def _tool_backends() -> list[dict]:
    """The models HomeShed itself is set up with (its /local-ai/backends: names, local or cloud, benched or not), for a
    panel without the owner's own server list. Status only: a set-up model that isn't benched counts as online. Kept
    for 30 seconds; empty when HomeShed can't be asked."""
    if time.time() - _backends_cache["at"] < 30:
        return _backends_cache["rows"]
    rows = []
    try:
        status, got = await _tool_json("/local-ai/backends")
        if status == 200:
            rows = [{"name": b["name"], "label": "cloud" if b.get("cloud") else "your own model",
                     "type": "gateway" if b.get("cloud") else "llamacpp", "online": not b.get("benched")}
                    for b in got.get("backends") or []
                    if isinstance(b, dict) and b.get("configured") and b.get("name")]
    except (RuntimeError, httpx.RequestError):
        pass
    _backends_cache.update(at=time.time(), rows=rows)
    return rows


@app.get("/api/local-ai")
async def local_ai_usage():
    if not LOCAL_AI_SERVERS:  # a public install: HomeShed's configured models, with status but no token counts
        blank = dict.fromkeys(("requests_processing", "requests_deferred", "requests_total", "prompt_tokens",
                               "output_tokens", "total_tokens", "delta_tokens_window", "avg_ms"))
        return {"servers": [{**b, **blank, "window_seconds": 0, "sparkline": []} for b in await _tool_backends()]}
    out = []
    for server in LOCAL_AI_SERVERS:
        hist = _local_ai_history[server["name"]]
        latest = hist[-1] if hist else {"online": False}
        first_online = next((s for s in hist if s.get("online")), None)
        delta_tokens = None
        if (
            latest.get("online")
            and first_online
            and latest.get("total_tokens") is not None
            and first_online.get("total_tokens") is not None
        ):
            delta_tokens = latest["total_tokens"] - first_online["total_tokens"]
        out.append(
            {
                "name": server["name"],
                "label": server["label"],
                "type": server["type"],
                "online": latest.get("online", False),
                "requests_processing": latest.get("requests_processing"),
                "requests_deferred": latest.get("requests_deferred"),
                "requests_total": latest.get("requests_total"),
                "prompt_tokens": latest.get("prompt_tokens"),
                "output_tokens": latest.get("output_tokens"),
                "total_tokens": latest.get("total_tokens"),
                "delta_tokens_window": delta_tokens,
                "window_seconds": (latest["timestamp"] - first_online["timestamp"]) if first_online else 0,
                "avg_ms": latest.get("avg_ms"),
                # A decision server counts decisions, the others tokens: each sparkline follows its own counter.
                "sparkline": [s.get("requests_total" if server["type"] == "decider" else "total_tokens")
                              for s in hist if s.get("online")],
            }
        )
    return {"servers": out}


def _not_connected(exc: RuntimeError, marks: tuple, say: str = "") -> JSONResponse:
    """A companion that isn't connected (no Docker socket, no Kuma database) is "not set up" with what to connect, not
    a failure to retry (the prepper's clean-VM test, 2026-10-01)."""
    text = _tool_message(exc)
    if any(m in text for m in marks):
        return JSONResponse({"error": say or text, "not_set_up": True})  # 200: not a failure (gate 6)
    return JSONResponse({"error": text}, status_code=502)


@app.get("/api/monitors")
async def monitors():
    try:
        return await _mcp_call_tool("uptime.status", {}, quiet=True)
    except RuntimeError as exc:
        return _not_connected(exc, ("Kuma database not found",),
                              "Uptime Kuma isn't connected. Point HomeShed at Kuma's database (KUMA_DB_PATH, mounted "
                              "read-only) and its checks show here.")


# ---------------------------------------------------------------------------
# Docker control -- reuses mcp-server's existing docker.container.* capabilities
# unchanged, including their built-in dry_run=true safety default (see
# capabilities/docker/restart.md's "Confirmation model"). This dashboard never
# defaults a write action to real -- the browser's own confirm step is what
# sets dry_run=false, same trust boundary as every other write path here.
# No "remove container" route: mcp-server has no such capability, and this
# panel isn't the place to add one.
# ---------------------------------------------------------------------------

@app.get("/api/docker/containers")
async def docker_containers():
    try:
        return await _mcp_call_tool("docker.container.list", {"all": True}, quiet=True)
    except RuntimeError as exc:
        return _not_connected(exc, ("socket isn't mounted", "can't read it"))


@app.post("/api/docker/containers/{action}")
async def docker_action(action: str, request: Request):
    if action not in {"start", "stop", "restart"}:
        return JSONResponse({"error": "unknown action"}, status_code=404)
    body = await request.json()
    container = body.get("container")
    if not container:
        return JSONResponse({"error": "container required"}, status_code=400)
    args = {"container": container, "dry_run": bool(body.get("dry_run", True))}
    if action == "restart" and "timeout" in body:
        args["timeout"] = body["timeout"]
    try:
        return await _mcp_call_tool(f"docker.container.{action}", args)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# Reasoning tab -- demos the two capabilities built 2026-09-24 toward the
# "local reasoning" architecture (see ARCHITECTURE.md). Both are read-risk,
# advisory-only capabilities (no dry_run, nothing to confirm) -- unlike the
# Docker tab, no confirm-gating needed here.
# ---------------------------------------------------------------------------

@app.post("/api/reasoning/solve")
async def reasoning_solve(request: Request):
    body = await request.json()
    smt_lib2 = body.get("smt_lib2")
    if not smt_lib2:
        return JSONResponse({"error": "smt_lib2 required"}, status_code=400)
    args = {"smt_lib2": smt_lib2}
    if "timeout_ms" in body:
        args["timeout_ms"] = body["timeout_ms"]
    try:
        return await _mcp_call_tool("reasoning.solve", args)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.post("/api/reasoning/decompose")
async def reasoning_decompose(request: Request):
    body = await request.json()
    task = body.get("task")
    if not task:
        return JSONResponse({"error": "task required"}, status_code=400)
    args = {"task": task}
    if "max_subtasks" in body:
        args["max_subtasks"] = body["max_subtasks"]
    try:
        return await _mcp_call_tool("reasoning.decompose_task", args)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.post("/api/reasoning/route")
async def reasoning_route(request: Request):
    body = await request.json()
    text = body.get("text")
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    args = {"text": text}
    if "token_estimate" in body:
        args["token_estimate"] = body["token_estimate"]
    if "tool_count" in body:
        args["tool_count"] = body["tool_count"]
    try:
        return await _mcp_call_tool("reasoning.route", args)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.post("/api/reasoning/delegate")
async def reasoning_delegate(request: Request):
    body = await request.json()
    task = body.get("task")
    if not task:
        return JSONResponse({"error": "task required"}, status_code=400)
    args = {"task": task}
    if "token_estimate" in body:
        args["token_estimate"] = body["token_estimate"]
    if "tool_count" in body:
        args["tool_count"] = body["tool_count"]
    try:
        return await _mcp_call_tool("reasoning.delegate", args)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# Observations -- mcp-server's observe.* log (capabilities/observe/log.md): rule
# violations and corrections, with repeat-violation escalation (RULE-D-PROJECTS-007).
# The owner can log a correction from here directly (source="owner") and resolve items.
# ---------------------------------------------------------------------------

_OBS_STATUSES = {"open", "actioned", "declined", "superseded", "parked"}


@app.get("/api/observations/summary")
async def observations_summary():
    """Badge count only. Reads mcp-server's /observations/summary route directly, like
    /api/activity, so it never shows up as an observe.list call in the activity feed."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{MCP_BASE_URL}/observations/summary",
                                    headers={"Host": MCP_HOST_HEADER, "Authorization": f"Bearer {MCP_AUTH_TOKEN}"})
        except httpx.RequestError:
            return JSONResponse({"error": "mcp-server unreachable"}, status_code=502)
    if resp.status_code != 200:
        return JSONResponse({"error": f"mcp-server returned {resp.status_code}"}, status_code=502)
    return resp.json()


@app.get("/api/observations")
async def observations_list(status: str = "all"):
    if status != "all" and status not in _OBS_STATUSES:
        return JSONResponse({"error": "unknown status"}, status_code=400)
    try:
        return await _mcp_call_tool("observe.list", {"status": status, "limit": 200}, quiet=True)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.post("/api/observations")
async def observations_log(request: Request):
    body = await request.json()
    title, issue = (body.get("title") or "").strip(), (body.get("issue") or "").strip()
    if not title or not issue:
        return JSONResponse({"error": "title and issue required"}, status_code=400)
    args = {"title": title, "issue": issue, "source": "owner"}
    for key in ("rule", "fix", "area"):
        if body.get(key):
            args[key] = str(body[key])
    try:
        return await _mcp_call_tool("observe.log", args)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.post("/api/observations/{obs_id}")
async def observations_update(obs_id: str, request: Request):
    body = await request.json()
    status = body.get("status")
    if status not in _OBS_STATUSES:
        return JSONResponse({"error": "unknown status"}, status_code=400)
    args = {"id": obs_id, "status": status}
    if body.get("resolution"):
        args["resolution"] = str(body["resolution"])
    try:
        return await _mcp_call_tool("observe.update", args)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# The helper service on the owner's PC (optional): rule packs, guard modes, Claude's context and past sessions.
GPU_SERVICE_URL = os.environ.get("GPU_SERVICE_URL", "").strip().rstrip("/")  # the helper PC; empty: not set up
GPU_SERVICE_TOKEN = os.environ.get("GPU_SERVICE_TOKEN", "")
_GPU_AUTH = {"X-GPU-Token": GPU_SERVICE_TOKEN} if GPU_SERVICE_TOKEN else {}


# ---------------------------------------------------------------------------
# Token savings strip (Ops tab). Three figures, each labelled for what it is:
#   measured  -- RTK's own count of command-output tokens it trimmed before Claude saw them
#   measured  -- tokens the local llama.cpp models processed (since each server's last start)
#   estimated -- mcp-server usage.py's sizes-only, rule-based estimate of Claude tokens avoided
# ---------------------------------------------------------------------------


@app.get("/api/context")
async def claude_context():
    """Each Claude Code session's context use, from the context monitor on your PC (Overview)."""
    if (off := _no_helper("Claude's context monitor")) is not None:
        return off
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{GPU_SERVICE_URL}/stats/context")
        if r.status_code == 200:
            return r.json()
        return JSONResponse({"error": f"GPU service returned HTTP {r.status_code}"}, status_code=502)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "GPU service unreachable"}, status_code=502)


# Announcements (the owner, 2026-10-01: "a small announcement area, where i can send an announcement to all active
# agents"): the PC helper posts it to the notice board every Claude session's hooks read. Owner only: neither route is in
# VIEWER_ROUTES, and a viewer can never POST.
@app.get("/api/announce")
async def announcements():
    if (off := _no_helper("Announcements")) is not None:
        return off
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{GPU_SERVICE_URL}/announce", headers=_GPU_AUTH)
        if r.status_code == 200:
            return r.json()
        return JSONResponse({"error": f"GPU service returned HTTP {r.status_code}"}, status_code=502)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "GPU service unreachable"}, status_code=502)


@app.post("/api/announce")
async def announce(request: Request):
    if (off := _no_helper("Announcements")) is not None:
        return off
    try:
        body = await request.json()
        body = body if isinstance(body, dict) else {}
    except ValueError:
        return JSONResponse({"error": "Send the announcement as JSON."}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{GPU_SERVICE_URL}/announce", json={"message": str(body.get("message") or "")[:2000]},
                                  headers=_GPU_AUTH)
        data = r.json()
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "Can't reach the helper on your PC."}, status_code=502)
    if r.status_code == 200:
        return data
    detail = data.get("detail") if isinstance(data, dict) and isinstance(data.get("detail"), str) else f"HTTP {r.status_code}"
    return JSONResponse({"error": detail}, status_code=r.status_code if r.status_code in (400, 401, 503) else 502)


@app.post("/api/announce/hide")
async def hide_announcements(request: Request):
    """Takes announcements out of the panel's history (the owner, 2026-10-01: "removeable entries or clear"):
    {"ids": [...]} or {"all": true}. The notices stay on the board the sessions read."""
    if (off := _no_helper("Announcements")) is not None:
        return off
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": 'Send {"ids": [...]} or {"all": true}.'}, status_code=400)
    send = {"all": True} if body.get("all") is True else {"ids": body.get("ids")}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{GPU_SERVICE_URL}/announce/hide", json=send, headers=_GPU_AUTH)
        data = r.json()
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "Can't reach the helper on your PC."}, status_code=502)
    if r.status_code == 200:
        return data
    detail = data.get("detail") if isinstance(data, dict) and isinstance(data.get("detail"), str) else f"HTTP {r.status_code}"
    return JSONResponse({"error": detail}, status_code=r.status_code if r.status_code in (400, 401, 503) else 502)


# Past Claude sessions and reopening one (the owner, 2026-09-29: "launch them back in their terminal and locations from a
# click of a button"). The GPU service's sessions.py does the work and every check; both routes are owner only (not
# in VIEWER_ROUTES): the list names projects and folders, and a viewer can never POST.
SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@app.get("/api/claude/sessions")
async def claude_sessions():
    if (off := _no_helper("Past sessions")) is not None:
        return off
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{GPU_SERVICE_URL}/sessions", headers=_GPU_AUTH)
        if r.status_code == 200:
            return r.json()
        return JSONResponse({"error": f"GPU service returned HTTP {r.status_code}"}, status_code=502)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "GPU service unreachable"}, status_code=502)


@app.post("/api/claude/sessions/{sid}/resume")
async def claude_session_resume(sid: str):
    if not SESSION_ID_RE.match(sid):
        return JSONResponse({"error": "That isn't a session id."}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{GPU_SERVICE_URL}/sessions/{sid}/resume", headers=_GPU_AUTH)
        body = r.json()
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "Can't reach the helper on your PC."}, status_code=502)
    if r.status_code == 200:
        return body
    detail = body.get("detail") if isinstance(body, dict) and isinstance(body.get("detail"), str) else f"HTTP {r.status_code}"
    return JSONResponse({"error": detail}, status_code=r.status_code if r.status_code in (400, 403, 404, 409, 429, 503) else 502)


@app.post("/api/claude/sessions/{sid}/compact")
async def claude_session_compact(sid: str):
    """Compact now (the owner, 2026-10-01: "how do i compact sessions memory"): the PC helper types into that session's
    own window, only between turns: a save-your-memory prompt, then "/compact" once that answer has finished, then a
    carry-on prompt once the compaction has. It answers after the first; the card follows the steps. Owner only (not
    in VIEWER_ROUTES)."""
    if not SESSION_ID_RE.match(sid):
        return JSONResponse({"error": "That isn't a session id."}, status_code=400)
    if (off := _no_helper("Compacting a session")) is not None:
        return off
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(f"{GPU_SERVICE_URL}/context/compact", json={"session": sid}, headers=_GPU_AUTH)
        body = r.json()
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "Can't reach the helper on your PC."}, status_code=502)
    if r.status_code == 200:
        return body
    detail = body.get("detail") if isinstance(body, dict) and isinstance(body.get("detail"), str) else f"HTTP {r.status_code}"
    return JSONResponse({"error": detail}, status_code=r.status_code if r.status_code in (400, 404, 409, 429, 501) else 502)


# --- Projects page (to-do #38): the folders Claude works in on the owner's PC, from the helper there (projects.py).
# Owner only (not in VIEWER_ROUTES): it names folders, agents and repos, and a viewer can never POST.
@app.get("/api/projects")
async def projects_list():
    if (off := _no_helper("The Projects page")) is not None:
        return off
    return await _main_pc("GET", "/projects")


@app.post("/api/projects/pins")
async def projects_pin(request: Request):
    body = await request.json()
    return await _main_pc("POST", "/projects/pins", {k: str(body.get(k) or "")[:500] for k in ("folder", "path", "note")})


@app.post("/api/projects/pins/remove")
async def projects_unpin(request: Request):
    body = await request.json()
    return await _main_pc("POST", "/projects/pins/remove", {k: str(body.get(k) or "")[:500] for k in ("folder", "path")})


# --- Settings page: one list gathered from everywhere a setting lives ---------------------------------
# Each item says where it's saved; a change goes back to that service. "main-pc" = the GPU service's
# settings.json next to Claude Code, "tool-server" = mcp-server's runtime settings, "panel" = here.
SETTING_SOURCES = {"main-pc": "your PC", "tool-server": "the tool server", "panel": "this Control Panel"}


async def _remote_settings(client: httpx.AsyncClient, source: str) -> tuple[dict | None, str | None]:
    url, headers = ((f"{GPU_SERVICE_URL}/settings", {}) if source == "main-pc"
                    else (f"{MCP_BASE_URL}/settings", _MCP_HEADERS))
    try:
        r = await client.get(url, headers=headers)
        if r.status_code == 200:
            return r.json(), None
        return None, f"{SETTING_SOURCES[source]} answered HTTP {r.status_code}"
    except (httpx.RequestError, ValueError):
        return None, f"{SETTING_SOURCES[source]} isn't reachable"


@app.get("/api/settings")
async def settings_all():
    items, info, errors = [], [], {}
    async with httpx.AsyncClient(timeout=5) as client:
        wanted = ("main-pc", "tool-server") if GPU_SERVICE_URL else ("tool-server",)  # no helper PC: nothing to ask
        fetched = await asyncio.gather(*(_remote_settings(client, s) for s in wanted))
    sources = dict(zip(wanted, fetched))
    sources["panel"] = ({"values": panel_values(), "schema": PANEL_SCHEMA}, None)
    for source, (data, error) in sources.items():
        if error:
            errors[source] = error
            continue
        for key, spec in (data.get("schema") or {}).items():
            items.append({**spec, "key": key, "source": source, "value": (data.get("values") or {}).get(key)})
        info.extend(data.get("info") or [])
    return {"items": items, "info": info, "errors": errors, "saved_on": SETTING_SOURCES}


@app.put("/api/settings")
async def settings_put(request: Request):
    try:
        body = await request.json()
        source, key, value = body["source"], body["key"], body["value"]
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"error": "Send {source, key, value}."}, status_code=400)
    if source == "panel":
        try:
            return {"source": source, "key": key, "value": panel_update({key: value})[key]}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    if source not in ("main-pc", "tool-server"):
        return JSONResponse({"error": f"Unknown place: {source}"}, status_code=400)
    url, headers = ((f"{GPU_SERVICE_URL}/settings", _GPU_AUTH) if source == "main-pc"
                    else (f"{MCP_BASE_URL}/settings", _MCP_HEADERS))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(url, json={key: value}, headers=headers)
    except httpx.RequestError:
        return JSONResponse({"error": f"{SETTING_SOURCES[source]} isn't reachable, so nothing changed."}, status_code=502)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        return JSONResponse({"error": data.get("detail") or data.get("error") or f"HTTP {r.status_code}"}, status_code=r.status_code)
    return {"source": source, "key": key, "value": (data.get("values") or {}).get(key, value)}


def _address(url: str) -> str:
    """scheme://host:port of a URL: what the Settings page shows (never paths, keys or tokens)."""
    from urllib.parse import urlsplit

    u = urlsplit(url)
    return f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}" if u.hostname else ""


@app.get("/api/settings/connections")
async def settings_connections():
    """The services this Control Panel talks to, and whether each answers right now (read-only; the
    addresses come from .env until the Setup page can change them)."""
    targets = [("HomeShed", f"{MCP_BASE_URL}/healthz", _MCP_HEADERS),
               ("Shared memory", MEMORY_CORE_BASE_URL and f"{MEMORY_CORE_BASE_URL}/health", {}),
               ("Helper PC", GPU_SERVICE_URL and f"{GPU_SERVICE_URL}/health", {}),
               ("Phone alerts (ntfy)", NTFY_HEALTH_URL, {})]
    targets += [(s.get("label") or s["name"], s["url"], {}) for s in LOCAL_AI_SERVERS if s.get("url")]

    async def check(client, name, url, headers):
        if not url:  # no address: not set up, which isn't a fault
            return {"name": name, "address": "", "ok": None, "not_set_up": True}
        try:
            r = await client.get(url, headers=headers)
            return {"name": name, "address": _address(url), "ok": r.status_code < 500}
        except httpx.RequestError:
            return {"name": name, "address": _address(url), "ok": False}

    async with httpx.AsyncClient(timeout=3) as client:
        rows = await asyncio.gather(*(check(client, *t) for t in targets))
    unique = {(r["name"], r["address"]): r for r in rows}  # two cloud models behind one gateway: one row
    return {"connections": list(unique.values())}


# --- The helper service on the owner's PC (rule packs, guard modes): an optional part. Without one, its pages say
# it isn't set up; owner only (these routes aren't in VIEWER_ROUTES).
def _no_helper(what: str = "This") -> JSONResponse | None:
    """With no helper service on the owner's PC, the pages that read it say so (grey "not set up"), never "retry"."""
    if GPU_SERVICE_URL:
        return None
    return JSONResponse({"error": f"{what} needs the helper service on your PC, which isn't set up on this install.",
                         "not_set_up": True})  # 200: nothing failed, so nothing shows as a failed request (gate 6)


async def _main_pc(method: str, path: str, body: dict | None = None):
    if not GPU_SERVICE_URL:
        return JSONResponse({"error": "This needs the helper service on your PC, which isn't set up.", "not_set_up": True},
                            status_code=503)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.request(method, f"{GPU_SERVICE_URL}{path}", json=body, headers=_GPU_AUTH)
    except httpx.RequestError:
        return JSONResponse({"error": "The helper service on your PC isn't answering right now."},
                            status_code=502)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        return JSONResponse({"error": data.get("detail") or data.get("error") or f"HTTP {r.status_code}"},
                            status_code=r.status_code)
    return data


# --- Guides (2026-09-28: a guide to the Control Panel for every user, kept centrally). Read from a central guide
# library (a Strapi site with public read) when its address is set on the
# Settings page, otherwise from the guides bundled with this install (dashboard/guides, shipped in the image).
GUIDES_DIR = Path(__file__).parent / "guides"


def _front_matter(text: str) -> tuple[dict, str]:
    """(fields, body) of a Markdown file that starts with a --- key: value --- block."""
    if not text.startswith("---"):
        return {}, text
    head, _, body = text[3:].partition("\n---")
    fields = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip()] = value.strip()
    return fields, body.lstrip("\n")


def _bundled_guides() -> list[dict]:
    out = []
    for f in sorted(GUIDES_DIR.glob("*.md")) if GUIDES_DIR.is_dir() else []:
        meta, body = _front_matter(f.read_text(encoding="utf-8"))
        out.append({"slug": meta.get("slug") or f.stem, "title": meta.get("title") or f.stem,
                    "audience": meta.get("audience") or "everyone", "updated": meta.get("updated"),
                    "body": body, "source": "bundled"})
    return out


def _library_rows(payload) -> list[dict]:
    """Guides from a Strapi response: v5 (flat rows) or v4 ({id, attributes})."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        a = row.get("attributes", row) if isinstance(row, dict) else {}
        if a.get("slug") and a.get("title"):
            out.append({"slug": a["slug"], "title": a["title"], "audience": a.get("audience") or "everyone",
                        "updated": a.get("updatedAt"), "order": a.get("order") or 0, "body": a.get("body") or "",
                        "source": "library"})
    return sorted(out, key=lambda g: (g["order"], g["title"]))


async def _guides() -> tuple[list[dict], str | None]:
    """(guides, note): the library's when it answers, plus any that came with this install under another name (an
    install's own guide, such as the owner's "Connect a new project", never has to be published; 2026-10-02); else the
    bundled ones with the reason."""
    url = panel_value("guide_library_url")
    if url:
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                r = await client.get(url, params={"sort": "order", "pagination[pageSize]": 100})
            if r.status_code == 200:
                rows = _library_rows(r.json())
                if rows:
                    have = {g["slug"] for g in rows}
                    return rows + [g for g in _bundled_guides() if g["slug"] not in have], None
                return _bundled_guides(), "The guide library has no guides yet, so the ones that came with this install are shown."
            note = f"The guide library answered HTTP {r.status_code}"
        except (httpx.RequestError, ValueError):
            note = "The guide library isn't reachable"
        return _bundled_guides(), note + ", so the guides that came with this install are shown."
    return _bundled_guides(), None


def _visible(guides: list[dict], request: Request) -> list[dict]:
    viewer = getattr(request.state, "role", None) == "viewer"
    return [g for g in guides if not (viewer and g["audience"] == "owner")]


@app.get("/api/guides")
async def guides_list(request: Request):
    guides, note = await _guides()
    return {"guides": [{k: g[k] for k in ("slug", "title", "audience", "updated", "source")} for g in _visible(guides, request)],
            "note": note}


@app.get("/api/guides/{slug}")
async def guide_get(slug: str, request: Request):
    guides, note = await _guides()
    guide = next((g for g in _visible(guides, request) if g["slug"] == slug), None)
    if guide is None:
        return JSONResponse({"error": f"No guide called {slug}."}, status_code=404)
    return {**{k: guide[k] for k in ("slug", "title", "audience", "updated", "source", "body")}, "note": note}


@app.get("/api/ui-settings")
async def ui_settings(request: Request):
    """The few settings the page itself needs, for every login (view-only too)."""
    v = panel_values()
    out = {k: v[k] for k in ("panel_name", "refresh_seconds", "animations", "viewers_see_savings", "viewers_see_context",
                             "client_default_rate")}
    owner = getattr(request.state, "role", None) != "viewer"
    for add in ADD_ONS["ui"]:  # the owner-only parts' own settings, some for the owner only
        out.update(add(v, owner))
    return out


@app.get("/api/rules")
async def rules_monitor():
    """Rule monitoring: every rule, how often its guards fired, and how requests followed the task
    protocol. Read from the GPU service on your PC, where Claude Code and the hooks run. Owner only."""
    if not GPU_SERVICE_URL:
        return JSONResponse({"error": "Rule monitoring needs the guard hooks and the helper service that records them, "
                                      "and neither is set up on this install.", "not_set_up": True})
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{GPU_SERVICE_URL}/stats/rules")
        if r.status_code == 200:
            return r.json()
        return JSONResponse({"error": f"GPU service returned HTTP {r.status_code}"}, status_code=502)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "GPU service unreachable"}, status_code=502)


async def _tool_json(path: str) -> tuple[int, dict]:
    resp = await _mcp_admin("GET", path)
    try:
        body = json.loads(resp.body)
    except ValueError:
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {}


@app.get("/api/packs")
async def packs_list():
    """Installed rule packs (your PC's packs.py), and what a Pro add-on offers where there is one. Owner only."""
    data = await _main_pc("GET", "/packs")
    for add in ADD_ONS["packs"]:
        data = await add(data)
    return data


@app.post("/api/packs/{action}")
async def packs_action(action: str, request: Request):
    """install | remove {slug}. Install: the tool server fetches the pack with the Settings key and your PC checks and
    saves it; without a connected account the PC fetches it with its own settings, as before. Owner only."""
    if action not in ("install", "remove"):
        return JSONResponse({"error": f"Unknown action: {action}"}, status_code=404)
    try:
        body = await request.json()
        slug = str(body.get("slug") or "")
    except (ValueError, AttributeError):
        return JSONResponse({"error": "Send {slug}."}, status_code=400)
    if action == "install":
        for add in ADD_ONS["pack_install"]:  # a Pro add-on fetches its own packs; None: not one of its
            done = await add(slug)
            if done is not None:
                return done
    return await _main_pc("POST", f"/packs/{action}", {"slug": slug})


@app.put("/api/rules/guard-modes")
async def rules_guard_modes(request: Request):
    """Set rule-pack guards to block / remind / off ({guard id: mode}). Kept on your PC, where the guard engine
    reads them on every call. Owner only (not in VIEWER_ROUTES)."""
    try:
        body = await request.json()
        if not isinstance(body, dict) or not body:
            raise ValueError
    except ValueError:
        return JSONResponse({"error": "Send {guard id: block|remind|off}."}, status_code=400)
    return await _main_pc("PUT", "/settings/guard-modes", body)


@app.get("/api/tools")
async def tools_status():
    """Tools page: every tool with its on/off switch, scope, calls, success rate and speed."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{MCP_BASE_URL}/tools/status", headers=_MCP_HEADERS)
        if r.status_code == 200:
            return r.json()
        return JSONResponse({"error": f"mcp-server returned HTTP {r.status_code}"}, status_code=502)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "mcp-server unreachable"}, status_code=502)


# Run page (to-do #50): the owner runs any tool by hand, with a form built from its inputs or a raw JSON body, and keeps
# named saved runs to repeat later. Owner only (not in VIEWER_ROUTES). The tool server applies its own switches and
# limits exactly as for any other call, and every run is in its activity log.
RUN_TOOL_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+){1,3}$")
RUN_MAX_ARGS, RUN_TIMEOUT_S, SAVED_RUNS_MAX = 32 * 1024, 180, 100
SAVED_RUNS_FILE = Path(os.environ.get("SAVED_RUNS_FILE") or PANEL_DATA_DIR / "saved-runs.json")
_saved_runs_lock = threading.Lock()


def _run_request(body) -> tuple[str, dict] | JSONResponse:
    tool, args = (body or {}).get("tool"), (body or {}).get("args") or {}
    if not isinstance(tool, str) or not 3 <= len(tool) <= 80 or not RUN_TOOL_RE.match(tool):
        return JSONResponse({"ok": False, "error": "Pick a tool from the list."}, status_code=400)
    if not isinstance(args, dict):
        return JSONResponse({"ok": False, "error": "The body must be a JSON object, like {\"name\": \"value\"}."},
                            status_code=400)
    if len(json.dumps(args)) > RUN_MAX_ARGS:
        return JSONResponse({"ok": False, "error": "That body is over 32 KB."}, status_code=400)
    return tool, args


@app.post("/api/run")
async def run_tool(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = None
    parsed = _run_request(body)
    if isinstance(parsed, JSONResponse):
        return parsed
    tool, args = parsed
    started = time.monotonic()
    try:
        result = await _mcp_call_tool(tool, args, timeout=RUN_TIMEOUT_S)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:2000], "ms": round((time.monotonic() - started) * 1000)})
    except httpx.TimeoutException:
        return JSONResponse({"ok": False, "error": f"No answer within {RUN_TIMEOUT_S} s. It may still be running.",
                             "ms": RUN_TIMEOUT_S * 1000})
    except httpx.HTTPError:
        return JSONResponse({"ok": False, "error": "The tool server isn't answering."}, status_code=502)
    return {"ok": True, "result": result, "ms": round((time.monotonic() - started) * 1000)}


def _saved_runs() -> list[dict]:
    try:
        rows = json.loads(SAVED_RUNS_FILE.read_text(encoding="utf-8")).get("runs") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    return [r for r in rows if isinstance(r, dict) and r.get("name") and r.get("tool")]


@app.get("/api/run/saved")
async def saved_runs_list():
    return {"runs": _saved_runs(), "max": SAVED_RUNS_MAX}


@app.post("/api/run/saved")
async def saved_runs_save(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = None
    parsed = _run_request(body)
    if isinstance(parsed, JSONResponse):
        return parsed
    tool, args = parsed
    name = " ".join(str((body or {}).get("name") or "").split())
    if not 1 <= len(name) <= 60:
        return JSONResponse({"ok": False, "error": "Give it a name of 1-60 characters."}, status_code=400)
    note = " ".join(str(body.get("note") or "").split())[:300]
    with _saved_runs_lock:
        rows = _saved_runs()
        exists = any(r["name"] == name for r in rows)
        if exists and body.get("replace") is not True:
            return JSONResponse({"ok": False, "error": f"There's already a saved run called {name!r}."}, status_code=409)
        if not exists and len(rows) >= SAVED_RUNS_MAX:
            return JSONResponse({"ok": False, "error": f"That's {SAVED_RUNS_MAX} saved runs; remove one first."},
                                status_code=409)
        rows = [r for r in rows if r["name"] != name] + [{"name": name, "tool": tool, "args": args, "note": note,
                                                          "saved": time.time()}]
        SAVED_RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SAVED_RUNS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"runs": rows}, indent=1), encoding="utf-8")
        os.replace(tmp, SAVED_RUNS_FILE)
    return {"ok": True, "name": name}


@app.delete("/api/run/saved/{name}")
async def saved_runs_delete(name: str):
    with _saved_runs_lock:
        rows = _saved_runs()
        keep = [r for r in rows if r["name"] != name]
        if len(keep) == len(rows):
            return JSONResponse({"ok": False, "error": "No saved run has that name."}, status_code=404)
        tmp = SAVED_RUNS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"runs": keep}, indent=1), encoding="utf-8")
        os.replace(tmp, SAVED_RUNS_FILE)
    return {"ok": True}


@app.post("/api/tools/{tool_id}/{action}")
async def tools_switch(tool_id: str, action: str):
    """Switch one tool on or off (an internal kill-switch; mcp-server enforces it for every client)."""
    if action not in ("enable", "disable") or not re.fullmatch(r"[a-z0-9_.]{3,80}", tool_id):
        return JSONResponse({"error": "bad request"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(f"{MCP_BASE_URL}/tools/{tool_id}/{action}", headers=_MCP_HEADERS)
        return JSONResponse(r.json(), status_code=r.status_code)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "mcp-server unreachable"}, status_code=502)


# ---------------------------------------------------------------------------
# API access (2026-09-28): client identities on the tool server (mcp-server clients.py). The
# dashboard holds the owner token, so these proxies are how the owner manages clients. Tokens returned by
# create/rotate pass straight through to the browser (shown once) and are never logged here.
# ---------------------------------------------------------------------------

_CLIENT_NAME = re.compile(r"[a-z][a-z0-9-]{1,31}")
_CLIENT_ACTIONS = {"rotate", "suspend", "resume", "limits", "preset-none", "preset-read", "preset-all",
                   "shared-read-on", "shared-read-off"}  # reading the shared memory, per app (2026-10-01)


MCP_UNREACHABLE = "mcp-server unreachable"


async def _mcp_admin(method: str, path: str, body: dict | None = None, timeout: float = 10) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(method, f"{MCP_BASE_URL}{path}", headers=_MCP_HEADERS, json=body)
        return JSONResponse(r.json(), status_code=r.status_code)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": MCP_UNREACHABLE}, status_code=502)


@app.get("/api/clients")
async def clients_list():
    return await _mcp_admin("GET", "/clients")


# --- Credentials (the owner, 2026-09-29: "Any ssh, any credentials to access anything, should be available in the control
# panel for owners only. So all Claude instances has access to the same"). The values live encrypted in the tool
# server's vault (mcp-server/vault.py); every Claude session uses them through the tools and never reads them. Owner
# only: none of these routes are in VIEWER_ROUTES. Showing a value re-checks the owner's password here, then sends
# the reveal key, which only this Control Panel holds.
VAULT_REVEAL_KEY = os.environ.get("VAULT_REVEAL_KEY", "")
VAULT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
REVEAL_LOCK_AFTER, REVEAL_LOCK_S = 5, 600
_reveal_failures: list[float] = []


def _vault_name_error(name: str) -> JSONResponse | None:
    # Checked here too: the name becomes part of a tool-server path, so "../clients" must never get through.
    if VAULT_NAME.match(name):
        return None
    return JSONResponse({"error": "A name is CAPITALS, digits and underscores, starting with a letter."}, status_code=400)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


# --- HomeShed Pro (public since 2026-10-01): the Pro page and Settings > Pro membership. The owner pastes a key made
# on the Pro website; the tool server checks it with the site and keeps it in its vault (mcp-server/mavis_pro.py). No
# password is ever asked for (R&D's security review: a fork asking for one could be phishing). Owner only: not in
# VIEWER_ROUTES, and a viewer can never POST.
async def _pro(method: str, path: str = "", body: dict | None = None) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(method, f"{MCP_BASE_URL}/pro{path}", headers=_MCP_HEADERS, json=body)
        return JSONResponse(r.json(), status_code=r.status_code, headers={"Cache-Control": "no-store"})
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "The tool server isn't answering. Try again in a minute."}, status_code=502)


@app.get("/api/pro")
async def pro_status(fresh: bool = False):
    return await _pro("GET", "?fresh=true" if fresh else "")


@app.post("/api/pro/connect")
async def pro_connect(request: Request):
    body = await _json_body(request)  # only the key goes on
    return await _pro("POST", "/connect", {"key": str(body.get("key") or "")})


@app.delete("/api/pro")
async def pro_disconnect():
    return await _pro("DELETE")


@app.get("/api/vault")
async def vault_list():
    return await _mcp_admin("GET", "/vault")


@app.put("/api/vault/{name}")
async def vault_set(name: str, request: Request):
    return _vault_name_error(name) or await _mcp_admin(
        "PUT", f"/vault/{name}", {**await _json_body(request), "by": "owner (control panel)"})


@app.delete("/api/vault/{name}")
async def vault_delete(name: str):
    return _vault_name_error(name) or await _mcp_admin("DELETE", f"/vault/{name}")


@app.post("/api/vault/{name}/generate")
async def vault_generate(name: str, request: Request):
    return _vault_name_error(name) or await _mcp_admin(
        "POST", f"/vault/{name}/generate", {**await _json_body(request), "by": "owner (control panel)"})


@app.post("/api/vault/{name}/reveal")
async def vault_reveal(name: str, request: Request):
    """The value, for the owner's eyes: the password is checked again first. Five wrong passwords in ten minutes
    pause revealing for ten minutes."""
    if (bad := _vault_name_error(name)) is not None:
        return bad
    now = time.time()
    _reveal_failures[:] = [t for t in _reveal_failures if now - t < REVEAL_LOCK_S]
    if len(_reveal_failures) >= REVEAL_LOCK_AFTER:
        return JSONResponse({"error": "Too many wrong passwords. Try again in a few minutes."}, status_code=429)
    password = str((await _json_body(request)).get("password") or "")
    if not _owner_ok(password):  # the .env password, or the saved one (a first-run install has no .env password)
        _reveal_failures.append(now)
        return JSONResponse({"error": "That password isn't right."}, status_code=403)
    if not VAULT_REVEAL_KEY:
        return JSONResponse({"error": "Showing values isn't set up yet: this Control Panel has no VAULT_REVEAL_KEY."},
                            status_code=503)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{MCP_BASE_URL}/vault/{name}/reveal",
                                  headers={**_MCP_HEADERS, "X-Vault-Reveal": VAULT_REVEAL_KEY})
        return JSONResponse(r.json(), status_code=r.status_code, headers={"Cache-Control": "no-store"})
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "mcp-server unreachable"}, status_code=502)


@app.post("/api/vault-test")
async def vault_test(request: Request):
    """Connect button: check an address or key works before saving it (mcp-server /vault-test)."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{MCP_BASE_URL}/vault-test", headers=_MCP_HEADERS, json=await _json_body(request))
        return JSONResponse(r.json(), status_code=r.status_code)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "mcp-server unreachable"}, status_code=502)


@app.get("/api/apis")
async def apis_catalog():
    """The shared API catalog (mcp-server apis.list): every API the platform can use, what it costs, whether it
    runs on your own machines and whether it works now. No secrets in it: auth names a credential, never a value."""
    try:
        return await _mcp_call_tool("apis.list", {}, quiet=True)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.get("/api/progress")
async def release_progress():
    """Each project's release progress, as the agent preparing it last reported (mcp-server repo.progress): the
    Overview's Release progress card. the owner, 2026-09-30: the prepper gives the updates, the dashboard shows the
    percentage. Owner only: it names projects and what waits on the owner.
    the owner, 2026-10-01: "where the project is being made (dir location)". A project that names its workspace gets
    its folder here, from the tool server's project list: progress data itself never holds a path."""
    try:
        data = await _mcp_call_tool("repo.progress", {}, quiet=True)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    rows = data.get("projects") if isinstance(data, dict) else None
    if rows and any(r.get("workspace") for r in rows):
        folders = await _project_folders()
        for r in rows:
            if folders.get(r.get("workspace")):
                r["folder"] = folders[r["workspace"]]
    return data


async def _project_folders() -> dict:
    """{registered name: folder} from the tool server's project list (the owner's own). Empty when it can't be read."""
    try:
        status, got = await _tool_json("/projects")
    except (RuntimeError, httpx.RequestError):
        return {}
    return {r["name"]: r["path"] for r in got.get("projects") or []
            if status == 200 and isinstance(r, dict) and r.get("name") and r.get("path")}


@app.get("/api/progress/{project}")
async def release_progress_one(project: str):
    """One project's full record for the card's "Show the gates": every gate with its evidence, and recent updates."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", project):
        return JSONResponse({"error": "no such project"}, status_code=404)
    try:
        return await _mcp_call_tool("repo.progress", {"project": project}, quiet=True)
    except RuntimeError as exc:
        message = _tool_message(exc)
        return JSONResponse({"error": message}, status_code=404 if "no progress" in message else 502)


TODO_PRO_ONLY = "comes with HomeShed Pro"  # the todo.* tools' refusal on the free plan (mcp-server tools/todo/store.py)


def _tool_message(exc: Exception) -> str:
    return re.sub(r"^Error executing tool [^:]+: ", "", str(exc))


@app.get("/api/todo")
async def todo_list(status: str = "open"):
    """The agent to-do list (mcp-server todo.*; the owner, 2026-09-30). HomeShed Pro only: on the free plan the tools
    refuse and this answers {pro: false, note} so the page shows its example list with the reason. Owner only: a
    view-only login gets the page's example list and never asks here (403)."""
    if status not in ("open", "closed", "all"):
        return JSONResponse({"error": "status must be open, closed or all"}, status_code=400)
    try:
        return await _mcp_call_tool("todo.list", {"status": status, "detail": "full", "limit": 100}, quiet=True)
    except RuntimeError as exc:
        if TODO_PRO_ONLY in str(exc):
            return {"pro": False, "note": _tool_message(exc)}
        if re.search(r"(?i)unknown tool", str(exc)):  # the public build ships without the Pro todo.* tools
            return {"pro": False, "note": "The to-do list comes with HomeShed Pro."}
        return JSONResponse({"error": _tool_message(exc)}, status_code=502)


@app.post("/api/todo")
async def todo_add(request: Request):
    body = await request.json()
    args = {k: str(body[k]) for k in ("text", "assignee", "priority", "repeat") if body.get(k)}
    if body.get("auto") is True:  # run without asking: only a real true (the owner, 2026-09-30)
        args["auto"] = True
    try:
        return await _mcp_call_tool("todo.add", args)
    except RuntimeError as exc:
        return JSONResponse({"error": _tool_message(exc)}, status_code=502)


@app.get("/api/todo/runner")
async def todo_runner():
    """The overnight runner, for the To-do page's status pill (the PC helper's todo_jobs.py): on, the hours, whether a
    task may start and why not, tonight's count, and the running task. Owner only: not on the view-only list."""
    if not GPU_SERVICE_URL:  # the runner lives on the helper PC: without one, the list still works, by hand
        return {"not_set_up": True, "on": False, "in_hours": False, "can_start": False,
                "why_not": "The overnight runner needs the helper PC, which isn't set up.", "running": []}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{GPU_SERVICE_URL}/todo/jobs", headers=_GPU_AUTH)
        if r.status_code != 200:
            return JSONResponse({"error": f"the PC's helper answered HTTP {r.status_code}"}, status_code=502)
        d = r.json()
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "the PC's helper service is unreachable"}, status_code=502)
    return {"on": bool(d.get("on")), "in_hours": bool(d.get("in_hours")), "hours": d.get("hours"),
            "can_start": bool(d.get("can_start")), "why_not": d.get("why_not") or "", "tonight": d.get("tonight"),
            "most": d.get("most"), "running": [{"id": j.get("id"), "assignee": j.get("assignee"),
                                                "started": j.get("started")} for j in d.get("running") or []]}


@app.post("/api/todo/runner/run-now")
async def todo_run_now():
    """The To-do page's "Run now" (R&D C25): the tool server runs the queue for one pass now, at any hour, then it's
    back to the overnight hours (mcp-server tools/todo/runner.py). Owner only: not on the view-only list."""
    return await _mcp_admin("POST", "/todo/run-now")


@app.post("/api/todo/{item_id}")
async def todo_change(item_id: int, request: Request):
    """Approve, reply, reassign, cancel, reprioritise or stop repeating a to-do (todo.update as the owner)."""
    body = await request.json()
    args = {"id": item_id, **{k: str(body[k]) for k in ("status", "comment", "assignee", "repeat", "priority")
                              if body.get(k)}}
    if isinstance(body.get("auto"), bool):  # run without asking, on or off
        args["auto"] = body["auto"]
    if body.get("approve") is True:
        args["approve"] = True
    try:
        return await _mcp_call_tool("todo.update", args)
    except RuntimeError as exc:
        return JSONResponse({"error": _tool_message(exc)}, status_code=502)


@app.get("/api/connections")
async def connections_list():
    """Who is connected to the tool server right now (Overview's Clients live, the API page)."""
    return await _mcp_admin("GET", "/connections")


@app.post("/api/clients")
async def clients_create(request: Request):
    body = await request.json()
    keep = {k: body.get(k) for k in ("name", "note", "preset", "expires_days", "rate_per_min") if k in body}
    return await _mcp_admin("POST", "/clients", keep)


@app.post("/api/clients/suspend-all")
async def clients_suspend_all():
    return await _mcp_admin("POST", "/clients/suspend-all")


@app.delete("/api/clients/{name}")
async def clients_delete(name: str):
    if not _CLIENT_NAME.fullmatch(name):
        return JSONResponse({"error": "bad client name"}, status_code=400)
    return await _mcp_admin("DELETE", f"/clients/{name}")


@app.post("/api/clients/{name}/{action}")
async def clients_action(name: str, action: str, request: Request):
    if not _CLIENT_NAME.fullmatch(name) or action not in _CLIENT_ACTIONS:
        return JSONResponse({"error": "bad request"}, status_code=400)
    body = None
    if action == "limits":
        raw = await request.json()
        body = {"rate_per_min": raw.get("rate_per_min"), "expires_days": raw.get("expires_days")}
    return await _mcp_admin("POST", f"/clients/{name}/{action}", body)


# Apps' writes to the shared memory, waiting for the owner (the owner's decision, 2026-10-01: each app has its own memory;
# reading the shared memory is on by default with a switch per app; writing to it needs his approval). Owner only:
# these routes aren't in VIEWER_ROUTES. Not under /api/memory/: GET /api/memory/{category} would catch them.
_WAITING_ID = re.compile(r"[0-9a-f]{8}")


@app.get("/api/shared-memory/waiting")
async def shared_memory_waiting():
    return await _mcp_admin("GET", "/memory/pending")


@app.post("/api/shared-memory/waiting/{item_id}/{action}")
async def shared_memory_decide(item_id: str, action: str):
    if not _WAITING_ID.fullmatch(item_id) or action not in ("approve", "decline"):
        return JSONResponse({"error": "bad request"}, status_code=400)
    return await _mcp_admin("POST", f"/memory/pending/{item_id}/{action}")


@app.get("/api/printify/waiting/{item_id}/thumb/{n}")
async def printify_thumb(item_id: str, n: int):
    """A small copy of a photo an app asks to upload to an Etsy listing (etsy.listing.update images), made when it
    was asked, so the owner sees exactly what he approves (2026-10-08). Owner only."""
    if not _WAITING_ID.fullmatch(item_id) or not 0 <= n < 10:
        return JSONResponse({"error": "bad request"}, status_code=400)
    item = next((i for i in await _admin_items("/printify/pending") or [] if str(i.get("id")) == item_id), None)
    thumbs = ((item or {}).get("payload") or {}).get("thumbs") or []
    try:
        data = base64.b64decode(thumbs[n], validate=True)
    except (IndexError, TypeError, ValueError):
        return JSONResponse({"error": "no such photo"}, status_code=404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/printify/waiting/{item_id}/{action}")
async def printify_decide(item_id: str, action: str):
    """A Printify publish an app asked for: approve publishes it to the shop (checked again first), decline drops it.
    Owner only."""
    if not _WAITING_ID.fullmatch(item_id) or action not in ("approve", "decline"):
        return JSONResponse({"error": "bad request"}, status_code=400)
    # An approval does the shop's work (Printify, Etsy: ~3 s each) and several clicked at once queue one after another
    # on the tool server, so it gets a long wait. If even that runs out, the card mustn't say it failed when it went
    # through (2026-10-08: 2 of 4 Etsy edits showed "502" yet were applied): ask whether it still waits.
    resp = await _mcp_admin("POST", f"/printify/pending/{item_id}/{action}", timeout=60)
    if resp.status_code != 502 or json.loads(resp.body).get("error") != MCP_UNREACHABLE:
        return resp  # an answer, a refusal, or a shop error (it stays waiting, with the reason)
    left = await _admin_items("/printify/pending")
    if left is not None and not any(str(i.get("id")) == item_id for i in left):
        return JSONResponse({"id": item_id, "approved": action == "approve", "slow": True,
                             "note": "Done: it's no longer waiting (the tool server was slow to answer)."})
    return JSONResponse({"error": "no answer from the tool server yet; it may still be working on it. Refresh in a "
                                  "minute: if the card has gone, it went through."}, status_code=504)


@app.post("/api/proxmox/waiting/{item_id}/{action}")
async def proxmox_decide(item_id: str, action: str):
    """A Proxmox change (power, snapshot, create) queued by a tool: approve sends it, decline drops it. Owner only."""
    if not _WAITING_ID.fullmatch(item_id) or action not in ("approve", "decline"):
        return JSONResponse({"error": "bad request"}, status_code=400)
    return await _mcp_admin("POST", f"/proxmox/pending/{item_id}/{action}")


async def _admin_items(path: str) -> list[dict] | None:
    """An admin route's "items" list, or None when it can't be read (that source is named as unavailable)."""
    resp = await _mcp_admin("GET", path)
    if resp.status_code != 200:
        return None
    try:
        items = json.loads(resp.body).get("items")
    except (ValueError, AttributeError):
        return None
    return items if isinstance(items, list) else None


# Replies on the to-do list (the owner, 2026-10-06: "i need to know when i have replies on todo list too, i dont know
# when. give me a dash thing like i do approvals"): helpers' comments newer than the owner last marked them read. The
# mark lives here, so his phone and PC agree. First use counts the last two days, not every old comment.
TODO_SEEN_FILE = Path(os.environ.get("TODO_SEEN_FILE") or PANEL_DATA_DIR / "todo-replies-seen.json")
REPLIES_FIRST_LOOKBACK_S = 2 * 86400
MAX_REPLIES = 50


def _todo_seen_at() -> float:
    try:
        return float(json.loads(TODO_SEEN_FILE.read_text(encoding="utf-8"))["seen_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return time.time() - REPLIES_FIRST_LOOKBACK_S


@app.get("/api/todo/replies")
async def todo_replies():
    """Comments by anyone but the owner on to-dos, newer than the read mark, newest first. Owner only."""
    seen = _todo_seen_at()
    try:
        data = await _mcp_call_tool("todo.list", {"status": "all", "detail": "full", "limit": 100}, quiet=True)
    except RuntimeError as exc:
        if TODO_PRO_ONLY in str(exc) or re.search(r"(?i)unknown tool", str(exc)):
            return {"replies": [], "count": 0, "seen_at": seen}
        return JSONResponse({"error": _tool_message(exc)}, status_code=502)
    rows = []
    for item in data.get("items") or []:
        comments = [c for c in item.get("comments") or [] if isinstance(c, dict)]
        # Replying on a task counts as reading it (the owner, 2026-10-06: "i replied but still see i have 16 new
        # replies?"): only helpers' comments after his own last one there are new.
        answered = max([float(c.get("at") or 0) for c in comments if c.get("by") == "owner"], default=0.0)
        for c in comments:
            if c.get("by") not in (None, "", "owner") and float(c.get("at") or 0) > max(seen, answered):
                rows.append({"id": item.get("id"), "task": str(item.get("text") or "")[:140], "by": c.get("by"),
                             "text": str(c.get("text") or "")[:400], "at": c.get("at"), "status": item.get("status")})
    rows.sort(key=lambda r: -float(r["at"] or 0))
    return {"replies": rows[:MAX_REPLIES], "count": len(rows), "seen_at": seen}


@app.post("/api/todo/replies/seen")
async def todo_replies_seen():
    """Mark every reply so far as read (for every device)."""
    TODO_SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TODO_SEEN_FILE.with_suffix(".tmp")
    now = time.time()
    tmp.write_text(json.dumps({"seen_at": now}), encoding="utf-8")
    os.replace(tmp, TODO_SEEN_FILE)
    return {"seen_at": now}


# Messages from agents (the owner, 2026-10-06: "a notification like the others for agents to leave me a message. i can
# reply to them through it too"): the tool server's inbox (owner_inbox.py), through its owner-only admin routes. Owner
# only here too (not in VIEWER_ROUTES).
@app.get("/api/inbox")
async def inbox():
    return await _mcp_admin("GET", "/inbox")


@app.post("/api/inbox/{item_id}/{action}")
async def inbox_answer(item_id: str, action: str, request: Request):
    if not _WAITING_ID.fullmatch(item_id) or action not in ("reply", "done"):
        return JSONResponse({"error": "bad request"}, status_code=400)
    body = None
    if action == "reply":
        try:
            raw = await request.json()
        except ValueError:
            raw = None
        text = raw.get("text") if isinstance(raw, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            return JSONResponse({"error": "write a reply (up to 2,000 characters)"}, status_code=400)
        body = {"text": text, "done": bool(raw.get("done"))}
    resp = await _mcp_admin("POST", f"/inbox/{item_id}/{action}", body)
    if action == "reply" and resp.status_code == 200:
        await _tell_sender(resp, text, body["done"])
    return resp


async def _tell_sender(resp: JSONResponse, text: str, done: bool) -> None:
    """The owner, 2026-10-06: "if i reply to messages from the dash do the agents know about this?" Now they're told:
    the PC helper posts a notice every session sees once (the named sender reads it with inbox.replies). Best effort:
    the reply itself is already saved."""
    try:
        item = json.loads(resp.body)
        if item.get("client") not in (None, "owner"):  # an app (another machine) checks inbox.replies itself
            return
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{GPU_SERVICE_URL}/inbox-reply", headers=_GPU_AUTH,
                              json={"sender": item.get("sender"), "message_id": item.get("id"), "text": text, "done": done})
    except (httpx.HTTPError, ValueError, AttributeError):
        pass


# Connect Etsy (the owner, 2026-10-06; public): the tool server runs Etsy's PKCE sign-in and keeps the tokens; this
# panel only starts it, receives Etsy's return (on https), or passes on the address he pasted. Owner only.
@app.get("/api/etsy")
async def etsy_status():
    return await _mcp_admin("GET", "/etsy/connect")


@app.post("/api/etsy/connect")
async def etsy_connect(request: Request):
    body = await _json_body(request)
    return await _mcp_admin("POST", "/etsy/connect", {"redirect_uri": str((body or {}).get("redirect_uri") or "")[:300]})


@app.post("/api/etsy/finish")
async def etsy_finish(request: Request):
    body = await _json_body(request)
    return await _mcp_admin("POST", "/etsy/finish", {"url": str((body or {}).get("url") or "")[:2000]})


@app.get("/api/etsy/callback")
async def etsy_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """Where Etsy sends the browser back (the panel on https). Finishes, then lands on Settings with the result."""
    from urllib.parse import quote
    if error or not code:
        why = error_description or error or "Etsy sent no code"
        return RedirectResponse(f"/#settings?etsy=failed&why={quote(why[:200])}", status_code=303)
    resp = await _mcp_admin("POST", "/etsy/finish", {"code": code[:500], "state": state[:200]})
    try:
        data = json.loads(resp.body)
    except (ValueError, AttributeError):
        data = {}
    if resp.status_code == 200:
        return RedirectResponse(f"/#settings?etsy=ok&shop={quote(str(data.get('shop_name') or '')[:80])}", status_code=303)
    return RedirectResponse(f"/#settings?etsy=failed&why={quote(str(data.get('error') or 'unknown')[:200])}", status_code=303)


# Connect Threads (the owner, 2026-10-06), the same relay as Connect Etsy. Owner only.
@app.get("/api/threads")
async def threads_status():
    return await _mcp_admin("GET", "/threads/connect")


@app.post("/api/threads/connect")
async def threads_connect(request: Request):
    body = await _json_body(request)
    return await _mcp_admin("POST", "/threads/connect", {"redirect_uri": str((body or {}).get("redirect_uri") or "")[:300]})


@app.post("/api/threads/finish")
async def threads_finish(request: Request):
    body = await _json_body(request)
    return await _mcp_admin("POST", "/threads/finish", {"url": str((body or {}).get("url") or "")[:2000]})


@app.get("/api/threads/callback")
async def threads_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """Where Threads sends the browser back (the panel on https)."""
    from urllib.parse import quote
    if error or not code:
        why = error_description or error or "Threads sent no code"
        return RedirectResponse(f"/#settings?threads=failed&why={quote(why[:200])}", status_code=303)
    resp = await _mcp_admin("POST", "/threads/finish", {"code": code[:600], "state": state[:200]})
    try:
        data = json.loads(resp.body)
    except (ValueError, AttributeError):
        data = {}
    if resp.status_code == 200:
        return RedirectResponse(f"/#settings?threads=ok&account={quote(str(data.get('username') or '')[:60])}", status_code=303)
    return RedirectResponse(f"/#settings?threads=failed&why={quote(str(data.get('error') or 'unknown')[:200])}", status_code=303)


@app.get("/api/approvals")
async def approvals():
    """Everything waiting for the owner's yes, in one list for the panel's approvals bar (the owner, 2026-10-06, to-do
    #51: "notified in the dash if something is waiting ... approve or deny from there"): to-do tasks waiting for an OK,
    apps' writes to the shared memory, and queued Proxmox changes. A source that can't be read is named in
    "unavailable" rather than failing the rest. Owner only (not in VIEWER_ROUTES)."""
    items, unavailable = [], []
    try:
        todo = await _mcp_call_tool("todo.list", {"status": "open", "detail": "compact", "limit": 100}, quiet=True)
        for t in todo.get("items") or []:
            if t.get("waiting_on_ok"):
                items.append({"kind": "todo", "id": str(t.get("id")), "title": str(t.get("text") or "")[:300],
                              "from": t.get("assignee") or "", "at": t.get("updated")})
    except RuntimeError as exc:
        if TODO_PRO_ONLY not in str(exc) and not re.search(r"(?i)unknown tool", str(exc)):
            unavailable.append("to-do list")
    shared = await _admin_items("/memory/pending")
    if shared is None:
        unavailable.append("shared memory")
    for i in shared or []:
        items.append({"kind": "memory", "id": str(i.get("id")), "title": str(i.get("content") or "")[:300],
                      "from": i.get("client") or "", "at": i.get("at")})
    prox = await _admin_items("/proxmox/pending")
    if prox is None:
        unavailable.append("Proxmox")
    for i in prox or []:
        items.append({"kind": "proxmox", "id": str(i.get("id")), "title": str(i.get("summary") or "")[:300],
                      "from": i.get("client") or "", "at": i.get("at")})
    pubs = await _admin_items("/printify/pending")  # to-do #59: a draft an app asks to publish to the shop
    if pubs is None:
        unavailable.append("Printify")
    for i in pubs or []:
        pay = i.get("payload") if isinstance(i.get("payload"), dict) else {}
        imgs = [m for m in (pay.get("send") or {}).get("images") or [] if isinstance(m, dict)][:10]
        thumbs = pay.get("thumbs") if isinstance(pay.get("thumbs"), list) else []
        shown = [{"label": str(m.get("image") or "").rsplit("/", 1)[-1][:80] or "photo",  # photos an app asks to upload
                  "thumb": f"/api/printify/waiting/{i.get('id')}/thumb/{n}" if n < len(thumbs) and thumbs[n] else None}
                 for n, m in enumerate(imgs)]
        items.append({"kind": "printify", "id": str(i.get("id")), "title": str(i.get("summary") or "")[:300],
                      **({"images": shown} if shown else {}),
                      "from": i.get("client") or "", "at": i.get("at"),
                      # a live product's update, or an Etsy listing's details, share Printify's approval queue
                      "action": i.get("action") if i.get("action") in ("update", "etsy") else "publish",
                      **({"error": str(i["last_error"])[:300]} if i.get("last_error") else {})})
    items.sort(key=lambda x: -float(x.get("at") or 0))
    return {"items": items, "count": len(items), "unavailable": unavailable}


@app.post("/api/clients/{name}/tools/{tool_id}/{action}")
async def clients_tool(name: str, tool_id: str, action: str):
    if not _CLIENT_NAME.fullmatch(name) or action not in ("grant", "revoke") or not re.fullmatch(r"[a-z0-9_.]{3,80}", tool_id):
        return JSONResponse({"error": "bad request"}, status_code=400)
    return await _mcp_admin("POST", f"/clients/{name}/tools/{tool_id}/{action}")


@app.get("/api/savings")
async def savings():
    """Savings page (2026-09-28, after OmniRoute's RTK/compression pages): RTK's per-day savings,
    RTK's "missed savings" scan of Claude Code history (redacted on the GPU service), and
    mcp-server's sizes-only estimates. Each part fails independently."""
    out: dict = {"daily": None, "discover": None, "estimate": None}
    async with httpx.AsyncClient(timeout=20) as client:
        for key, url in (("daily", f"{GPU_SERVICE_URL}/stats/rtk/daily"), ("discover", f"{GPU_SERVICE_URL}/stats/rtk/discover")):
            if not GPU_SERVICE_URL:  # RTK's numbers come from the helper PC: none set up, no numbers (never zeros)
                out[key] = {"available": False, "not_set_up": True}
                continue
            try:
                r = await client.get(url, timeout=100 if key == "discover" else 20)
                out[key] = r.json() if r.status_code == 200 else {"available": False, "error": f"HTTP {r.status_code}"}
            except (httpx.RequestError, ValueError):
                out[key] = {"available": False, "error": "GPU service unreachable"}
        try:
            r = await client.get(f"{MCP_BASE_URL}/usage", headers=_MCP_HEADERS)
            out["estimate"] = r.json() if r.status_code == 200 else None
        except (httpx.RequestError, ValueError):
            pass
    return out


@app.get("/api/tokens")
async def tokens():
    out: dict = {"rtk": None, "estimate": None, "local_models": []}
    async with httpx.AsyncClient(timeout=15) as client:
        if not GPU_SERVICE_URL:
            out["rtk"] = {"available": False, "not_set_up": True}
        else:
            try:
                r = await client.get(f"{GPU_SERVICE_URL}/stats/rtk")
                out["rtk"] = r.json() if r.status_code == 200 else {"available": False}
            except (httpx.RequestError, ValueError):
                out["rtk"] = {"available": False}
        try:
            r = await client.get(f"{MCP_BASE_URL}/usage",
                                 headers={"Host": MCP_HOST_HEADER, "Authorization": f"Bearer {MCP_AUTH_TOKEN}"})
            out["estimate"] = r.json() if r.status_code == 200 else None
        except (httpx.RequestError, ValueError):
            out["estimate"] = None
    out["cloud_models"] = []
    for server in LOCAL_AI_SERVERS:
        if server["type"] == "decider":
            continue  # decisions, not tokens
        latest = (_local_ai_history.get(server["name"]) or [{}])[-1]
        life = _lifetime.get(server["name"], {})
        row = {"name": server["name"], "online": latest.get("online", False),
               "prompt_tokens": life.get("prompt", 0), "output_tokens": life.get("output", 0)}
        out["local_models" if server["type"] == "llamacpp" else "cloud_models"].append(row)
    return out


# ---------------------------------------------------------------------------
# Models tab (2026-09-26, the owner: "some button on the frontend ... to turn the models on", "add
# safeguards so I don't blow up my GPU"). Local model containers + the GPU service are switched
# through the GPU service (allow-listed, token-protected, safeguard-checked). The delegator's
# model list comes from mcp-server, the same registry local_ai.ask uses.
# ---------------------------------------------------------------------------

@app.get("/api/models")
async def models_overview():
    out: dict = {"delegator": [], "errors": []}
    async with httpx.AsyncClient(timeout=20) as client:
        for add in ADD_ONS["models"]:  # the helper PC's readings and switches, where there is one
            out.update(await add(client, out["errors"]))
        try:
            r = await client.get(f"{MCP_BASE_URL}/local-ai/backends",
                                 headers={"Host": MCP_HOST_HEADER, "Authorization": f"Bearer {MCP_AUTH_TOKEN}"})
            out["delegator"] = r.json().get("backends", []) if r.status_code == 200 else []
        except (httpx.RequestError, ValueError):
            out["errors"].append("mcp-server unreachable")
    return out


# The page's own version: a hash of index.html as this server started. /api/health reports it, so a tab opened before a
# redeploy notices (found 2026-10-01: an old tab showed "Busy" through a whole compact, until the owner refreshed).
try:
    PANEL_VERSION = hashlib.sha256((Path(__file__).parent / "static" / "index.html").read_bytes()).hexdigest()[:12]
except OSError:
    PANEL_VERSION = ""


@app.get("/api/health")
async def health():
    out = {"mcp_server": "unknown", "memory_core": "unknown" if MEMORY_CORE_BASE_URL else BUILT_IN,
           "panel": PANEL_VERSION}
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            # /healthz is a cheap liveness route (2026-09-28); /capabilities did real work every 4 s.
            r = await client.get(f"{MCP_BASE_URL}/healthz", headers=_MCP_HEADERS)
            out["mcp_server"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
        except httpx.RequestError:
            out["mcp_server"] = "unreachable"
        if MEMORY_CORE_BASE_URL:
            try:
                r = await client.get(f"{MEMORY_CORE_BASE_URL}/health")
                out["memory_core"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
            except httpx.RequestError:
                out["memory_core"] = "unreachable"
    return out


# ---------------------------------------------------------------------------
# Delegator explainability + feature health (2026-09-28, ideas taken from OmniRoute's route-
# explainability, runtime and health/degradation panels; see .claude/external-audit-omniroute.md)
# ---------------------------------------------------------------------------


@app.get("/api/delegator/decisions")
async def delegator_decisions(limit: int = 25):
    """local_ai.ask's recent choices: which model answered and why the others were skipped."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{MCP_BASE_URL}/local-ai/decisions", params={"limit": limit}, headers=_MCP_HEADERS)
        if r.status_code == 200:
            return r.json()
        return JSONResponse({"error": f"mcp-server returned HTTP {r.status_code}"}, status_code=502)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "mcp-server unreachable"}, status_code=502)


@app.post("/api/delegator/{name}/reset")
async def delegator_reset(name: str):
    """The "try again now" button for a benched model: lifts its cooldown on mcp-server."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name):
        return JSONResponse({"error": "bad model name"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"{MCP_BASE_URL}/local-ai/backends/{name}/reset", headers=_MCP_HEADERS)
        return JSONResponse(r.json(), status_code=r.status_code)
    except (httpx.RequestError, ValueError):
        return JSONResponse({"error": "mcp-server unreachable"}, status_code=502)


def _latest_online(kind: str) -> bool:
    for server in LOCAL_AI_SERVERS:
        if server["type"] != kind:
            continue
        hist = _local_ai_history.get(server["name"]) or []
        if hist and hist[-1].get("online"):
            return True
    return False


def _feature(label: str, status: str, detail: str) -> dict:
    return {"label": label, "status": status, "detail": detail}


# --- What's installed (packaging-plan "Release UX: only show what's installed"; the owner 2026-09-28: "if this goes live,
# not all will have these abilities"). Installed means configured, not "answering right now" (/api/features says that):
# a page or Settings group for a part this setup doesn't have is hidden, while a part that's merely down keeps its page
# and says why. An unknown answer (a service briefly down) keeps the last known one, so nothing flickers away.
_installed_cache: dict = {"at": 0.0, "data": None}
_installed_known: dict = {}
INSTALLED_TTL_S = 60


@app.get("/api/installed")
async def installed():
    now = time.time()
    if _installed_cache["data"] and now - _installed_cache["at"] < INSTALLED_TTL_S:
        return _installed_cache["data"]
    types = {s.get("type") for s in LOCAL_AI_SERVERS}
    measured = {"local_model": "llamacpp" in types, "cloud": "gateway" in types}
    async with httpx.AsyncClient(timeout=3) as client:
        for add in ADD_ONS["installed"]:
            measured.update(await add(client))
    data = {k: (v if v is not None else _installed_known.get(k, True)) for k, v in measured.items()}
    _installed_known.update({k: v for k, v in measured.items() if v is not None})
    _installed_cache.update(at=now, data=data)
    return data


@app.get("/api/features")
async def features():
    """What works right now, in plain words: each feature is ok, degraded (running on its fallback)
    or down, with one sentence saying why. Built from what the dashboard already polls, plus one
    quick GPU-service check."""
    h = await health()
    mcp_ok, mem_ok = h["mcp_server"] == "ok", h["memory_core"] == "ok"
    # Set up means it has an address; "off" (grey, with how to add it) for a part that hasn't, "down" (red) only for
    # a part that has one and doesn't answer (release gate B: a first run must not look broken).
    if LOCAL_AI_SERVERS:  # the owner's own servers, polled here
        local_ok, cloud_ok = _latest_online("llamacpp"), _latest_online("gateway")
        has = {kind: any(s["type"] == kind and s.get("url") for s in LOCAL_AI_SERVERS) for kind in ("llamacpp", "gateway")}
    else:  # the models HomeShed itself is set up with
        rows = await _tool_backends()
        local_ok = any(r["online"] for r in rows if r["type"] == "llamacpp")
        cloud_ok = any(r["online"] for r in rows if r["type"] == "gateway")
        has = {"llamacpp": any(r["type"] == "llamacpp" for r in rows),
               "gateway": any(r["type"] == "gateway" for r in rows)}
    f = [_feature("Tools", "ok", "HomeShed is answering.") if mcp_ok else
         _feature("Tools", "down", "HomeShed isn't answering, so no tools work.")]
    if local_ok:
        f.append(_feature("Ask", "ok", "Your local model answers questions."))
    elif cloud_ok:
        f.append(_feature("Ask", "degraded", "Questions go to a cloud model because your local model is off."))
    elif not (has["llamacpp"] or has["gateway"]):
        f.append(_feature("Ask", "off", "No model is set up yet: add your own model server (Ollama, LM Studio or "
                                        "llama.cpp) or a cloud key in Settings."))
    else:
        f.append(_feature("Ask", "down", "No model can answer: the local model and the cloud models are unreachable."))
    for add in ADD_ONS["features"]:  # decisions, search, images: where this setup has them
        f += await add({"local_ok": local_ok, "cloud_ok": cloud_ok, "has": has})
    if h["memory_core"] == BUILT_IN:  # HomeShed's own store: it works whenever HomeShed does
        f.append(_feature("Memory", "ok", "Memory is built into HomeShed.") if mcp_ok else
                 _feature("Memory", "down", "Memory is built into HomeShed, which isn't answering."))
    else:
        f.append(_feature("Memory", "ok", "Shared memory is answering.") if mem_ok else
                 _feature("Memory", "down", "Shared memory isn't answering."))
    if not NTFY_HEALTH_URL:
        f.append(_feature("Alerts", "off", "Phone alerts aren't set up: add an ntfy server in Settings, Notifications."))
    else:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                ntfy_ok = (await client.get(NTFY_HEALTH_URL)).json().get("healthy") is True
        except (httpx.RequestError, ValueError, AttributeError):
            ntfy_ok = False
        f.append(_feature("Alerts", "ok", "Phone alerts (ntfy) are working.") if ntfy_ok else
                 _feature("Alerts", "down", "Phone alerts are down: ntfy isn't answering its health check."))
    return {"features": f}


# Resolved from this file, not the working directory: tests run from the repo root (found by the
# local coding agent 2026-09-28) used to fail with "Directory static does not exist".
try:
    import private_routes  # the owner's own pages' routes: left out of the public copy
except ImportError:
    pass
else:
    private_routes.register(app)

mimetypes.add_type("application/manifest+json", ".webmanifest")  # the app manifest's own type (not in every OS's table)
for _type, _ext in (("video/webm", ".webm"), ("video/mp4", ".mp4"), ("image/webp", ".webp")):  # the background art
    mimetypes.add_type(_type, _ext)
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
