"""The Terminal tab: SSH in the browser to the machines on your own network. xterm.js draws it in the page; this side
is one WebSocket route and asyncssh: one asyncio task per open session, no threads, no extra container.

Safety:
- Off until the owner switches "Terminal" on (Settings > Control Panel); owner only, never view-only logins or API
  clients. The WebSocket checks the owner's cookie and that it was opened by the panel's own page (Origin).
- From outside the home network (a proxied request, e.g. through a tunnel) the owner's password is asked again before
  the first session, and every UNLOCK_S after.
- Logins: the install makes its own SSH key once (ed25519, KEY_FILE, owner-only, never sent to the page). "Set up
  access" takes a machine's password once, adds the install's public key to that machine's authorized_keys and forgets
  the password. After that it signs in with the key.
- Host keys are trusted on first use and remembered (KNOWN_FILE); a changed key refuses the connection with a warning.
- Nothing typed or shown is logged. Opens and closes are.

Claude Code: "Claude" opens the same SSH session and types `claude` into the machine's shell (after `cd "folder"`
when one is given; the folder is checked against DIR_RE as it's typed). Claude Code must be installed and signed in on
that machine: the Terminal tab's "Claude Code on your machines" box has the steps, Windows included.

Memory: each session keeps only the last BUFFER_BYTES of output (to redraw the screen after a reload). A session whose
page went away is kept DETACHED_S, then closed; an untouched one is closed after IDLE_S; at most MAX_SESSIONS open.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

router = APIRouter()
SETTING = "terminal_enabled"
MAX_SESSIONS = int(os.environ.get("TERMINAL_MAX_SESSIONS") or 8)
BUFFER_BYTES = 256 * 1024
DETACHED_S = 300
CLAUDE_DETACHED_S = 1800  # a Claude session can keep working after its page closes
IDLE_S = 1800
UNLOCK_S = 12 * 3600
UNLOCK_COOKIE = "term_unlock"
HOST_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
                     r"|^\d{1,3}(?:\.\d{1,3}){3}$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")  # Linux, or a Windows user name (OpenSSH Server)
# The folder Claude Code starts in: a full path with no quotes or shell characters (it's typed into the shell)
DIR_RE = re.compile(r"^(?:/|[A-Za-z]:\\)[A-Za-z0-9 /\\._()-]{0,200}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ()._-]{0,39}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
SID_RE = re.compile(r"^[a-f0-9]{16}$")


def _m():
    import main
    return main


def _dir() -> Path:
    return Path(os.environ.get("TERMINAL_DIR") or _m().PANEL_DATA_DIR)


def _key_file() -> Path:
    return _dir() / "terminal-key"


def _hosts_file() -> Path:
    return _dir() / "terminal-hosts.json"


def _known_file() -> Path:
    return _dir() / "terminal-known-hosts.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def enabled() -> bool:
    return bool(_m().panel_value(SETTING))


# --- the install's own key ------------------------------------------------------------------------------------------

def private_key():
    """The install's SSH key, made on first use (ed25519). Its private half never leaves this file."""
    import asyncssh
    f = _key_file()
    if f.is_file():
        return asyncssh.read_private_key(str(f))
    key = asyncssh.generate_private_key("ssh-ed25519", comment="homeshed-terminal")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(key.export_private_key())
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return key


def public_key() -> str:
    return private_key().export_public_key().decode().strip()


# --- machines -------------------------------------------------------------------------------------------------------

def hosts() -> list[dict]:
    return [h for h in _read(_hosts_file(), {}).get("hosts", []) if isinstance(h, dict) and h.get("id")]


def _host(hid: str) -> dict | None:
    return next((h for h in hosts() if h["id"] == hid), None)


def check_host(body: dict) -> dict:
    name = " ".join(str(body.get("name") or "").split())
    host, user = str(body.get("host") or "").strip(), str(body.get("user") or "").strip()
    try:
        port = int(body.get("port") or 22)
    except (TypeError, ValueError):
        port = 0
    if not NAME_RE.match(name):
        raise ValueError("Give it a name: letters, numbers, spaces, ( ) . _ - (up to 40).")
    if not HOST_RE.match(host) or len(host) > 253:
        raise ValueError("The address is a name like server.lan, or the machine's IP address.")
    if not 1 <= port <= 65535:
        raise ValueError("The port is a number from 1 to 65535 (SSH is usually 22).")
    if not USER_RE.match(user):
        raise ValueError("The user is the machine's user name, like root, debian or your Windows user name.")
    hid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or secrets.token_hex(4)
    return {"id": hid, "name": name, "host": host, "port": port, "user": user,
            "found": str(body.get("found") or "manual")[:20]}


def save_host(body: dict) -> dict:
    h = check_host(body)
    rows = [x for x in hosts() if x["id"] != h["id"]]
    if len(rows) >= 200:
        raise ValueError("That's 200 machines already: remove some first.")
    _write(_hosts_file(), {"hosts": rows + [h]})
    return h


def remember_claude_dir(hid: str, folder: str) -> None:
    rows = hosts()
    for x in rows:
        if x["id"] == hid:
            x["claude_dir"] = folder
    _write(_hosts_file(), {"hosts": rows})


def remove_host(hid: str) -> bool:
    rows = hosts()
    left = [x for x in rows if x["id"] != hid]
    _write(_hosts_file(), {"hosts": left})
    return len(left) != len(rows)


async def suggested() -> list[dict]:
    """Machines the install already knows that aren't saved yet: the Proxmox nodes (their addresses from the
    cluster's own status), from the tool server's Proxmox monitor when it's set up."""
    saved = {(h["host"], h["port"]) for h in hosts()}
    out = []
    try:
        import httpx
        m = _m()
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(f"{m.MCP_BASE_URL}/proxmox/monitor", headers=m._MCP_HEADERS)
        data = r.json() if r.status_code == 200 else {}
    except Exception:  # no tool server or no Proxmox: nothing to suggest
        data = {}
    for n in data.get("nodes") or []:
        ip = n.get("ip")
        if ip and (ip, 22) not in saved and HOST_RE.match(str(ip)):
            out.append({"name": str(n.get("node"))[:40], "host": ip, "port": 22, "user": "root", "found": "proxmox"})
    return out


# --- host keys: trusted on first use ---------------------------------------------------------------------------------

def _fingerprint(key) -> str:
    return key.get_fingerprint("sha256")


def _client_class():
    import asyncssh

    class Client(asyncssh.SSHClient):
        def __init__(self, where: str, key=None):
            self.where, self.problem, self._key = where, None, key

        def public_key_auth_requested(self):
            """The install's own key, offered once. It comes from here, not client_keys: asyncssh 2.24 consults the
            host's ssh-agent whenever client_keys is set (agent_path=None doesn't stop it), and the panel must only
            ever use its own key (2026-10-13; on Windows the tests also tripped the write guard on the agent pipe)."""
            key, self._key = self._key, None
            return key

        def validate_host_public_key(self, host, addr, port, key) -> bool:
            known = _read(_known_file(), {})
            fp = _fingerprint(key)
            have = known.get(self.where)
            if have is None:  # first time: remember it
                known[self.where] = fp
                _write(_known_file(), known)
                return True
            if secrets.compare_digest(have, fp):
                return True
            self.problem = (f"This machine's identity changed (was {have}, now {fp}). That happens after a reinstall, "
                            "or when something is pretending to be it. If you expected it, press Forget its old key.")
            return False
    return Client


async def connect(h: dict, password: str | None = None):
    """An SSH connection to a saved machine: with the install's key, or once with a password (Set up access)."""
    import asyncssh
    where = f"{h['host']}:{h['port']}"
    client = None

    key = None if password else private_key()

    def factory():
        nonlocal client
        client = _client_class()(where, key)
        return client
    if password:  # once, for Set up access
        auth = dict(password=password, preferred_auth="password,keyboard-interactive")
    else:
        auth = dict(password=None, preferred_auth="publickey")
    # client_keys=None keeps asyncssh away from the host's ssh-agent; the key comes from public_key_auth_requested.
    try:
        conn, _ = await asyncssh.create_connection(factory, h["host"], h["port"], username=h["user"],
                                                   known_hosts=([], [], []), connect_timeout=10,
                                                   keepalive_interval=30, client_keys=None, agent_path=None, **auth)
        return conn
    except asyncssh.HostKeyNotVerifiable:
        raise ConnectionError((client and client.problem) or "The machine's identity couldn't be checked.") from None
    except asyncssh.PermissionDenied:
        raise ConnectionError("The machine refused the login." + ("" if password else
                              " Press Set up access once to let this Control Panel's key in.")) from None
    except (OSError, asyncio.TimeoutError, asyncssh.Error) as exc:
        raise ConnectionError(f"Couldn't reach {h['host']}:{h['port']} ({type(exc).__name__}).") from None


async def setup_access(h: dict, password: str) -> dict:
    """Signs in once with the password, adds the install's public key to authorized_keys (once), checks the key now
    works, and forgets the password."""
    pub = public_key()
    conn = await connect(h, password=password)
    try:
        quoted = "'" + pub.replace("'", "") + "'"
        cmd = ("umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
               f"(grep -qxF {quoted} ~/.ssh/authorized_keys || echo {quoted} >> ~/.ssh/authorized_keys)")
        result = await conn.run(cmd, check=False, timeout=20)
        if result.exit_status != 0:
            raise ConnectionError("The key couldn't be added on that machine: " + str(result.stderr or "")[:160])
    finally:
        conn.close()
    conn = await connect(h)  # the key must work now
    conn.close()
    return {"ok": True, "host": h["id"]}


def forget_key(h: dict) -> None:
    known = _read(_known_file(), {})
    known.pop(f"{h['host']}:{h['port']}", None)
    _write(_known_file(), known)


# --- real memory for the Proxmox tab ----------------------------------------------------------------------------------

MEM_CACHE_S = 60
_mem: dict[str, tuple[float, tuple[float, float] | None]] = {}


def parse_meminfo(text: str) -> tuple[float, float] | None:
    """(MemTotal, MemAvailable) in bytes from /proc/meminfo, or None."""
    f = {}
    for line in str(text).splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable") and v.split():
            try:
                f[k] = float(v.split()[0]) * 1024
            except ValueError:
                return None
    return (f["MemTotal"], f["MemAvailable"]) if len(f) == 2 and f["MemTotal"] > 0 else None


async def meminfo(h: dict) -> tuple[float, float] | None:
    """A saved machine's real memory (total, available), read with the install's key: the owner, 2026-10-10, "can you
    not get real ram figures for my dash?". Proxmox only knows a VM's memory with its page cache counted as used. One
    read-only command, cached MEM_CACHE_S; None when the machine can't be reached that way."""
    hit = _mem.get(h["id"])
    if hit and time.time() - hit[0] < MEM_CACHE_S:
        return hit[1]
    out = None
    try:
        conn = await asyncio.wait_for(connect(h), 8)
        try:
            r = await conn.run("cat /proc/meminfo", check=False, timeout=5)
            out = parse_meminfo(r.stdout) if r.exit_status == 0 else None
        finally:
            conn.close()
    except (ConnectionError, OSError, asyncio.TimeoutError, Exception):  # noqa: BLE001 - unreachable: no figure
        out = None
    _mem[h["id"]] = (time.time(), out)
    return out


async def real_memory(guests: list[dict]) -> int:
    """Gives each running VM that's a saved machine (matched by IP address) its real use: mem = total - available,
    mem_basis "available", mem_total its own MemTotal. Returns how many it filled in."""
    by_ip = {h["host"]: h for h in hosts()}
    pairs = []
    for g in guests:
        if g.get("status") == "running" and g.get("mem_basis") != "available":
            h = next((by_ip[ip] for ip in g.get("ips") or [] if ip in by_ip), None)
            if h:
                pairs.append((g, h))
    got = await asyncio.gather(*(meminfo(h) for _, h in pairs))
    n = 0
    for (g, _), m in zip(pairs, got):
        if m:
            g["mem"], g["mem_total"], g["mem_basis"] = m[0] - m[1], m[0], "available"
            n += 1
    return n


# --- sessions ---------------------------------------------------------------------------------------------------------

class Session:
    def __init__(self, sid: str, host: dict, claude_dir: str | None = None):
        """claude_dir: None for a plain shell; "" or a folder to start Claude Code (in that folder)."""
        self.id, self.host, self.claude_dir = sid, host, claude_dir
        self.conn = self.proc = self.ws = None
        self.buf = bytearray()
        self.opened = self.active = time.time()
        self.detached_at: float | None = None
        self.task: asyncio.Task | None = None
        self.ended = False

    def public(self) -> dict:
        return {"id": self.id, "host": self.host["id"], "name": self.name, "opened": self.opened,
                "attached": self.ws is not None, "claude": self.claude_dir is not None}

    @property
    def name(self) -> str:
        return ("Claude · " if self.claude_dir is not None else "") + self.host["name"]

    async def start(self, cols: int, rows: int) -> None:
        self.conn = await connect(self.host)
        self.proc = await self.conn.create_process(term_type="xterm-256color", term_size=(cols, rows), encoding=None)
        self.task = asyncio.create_task(self._pump())
        if self.claude_dir is not None:  # typed into the user's own shell, so its PATH and profile apply (bash,
            # PowerShell or cmd alike); when Claude exits, the shell is still there
            self.proc.stdin.write(((f'cd "{self.claude_dir}"\r' if self.claude_dir else "") + "claude\r").encode())

    async def _pump(self) -> None:
        try:
            while True:
                data = await self.proc.stdout.read(16384)
                if not data:
                    break
                self.buf += data
                if self.claude_dir is not None:  # Claude working on its own counts as use
                    self.active = time.time()
                if len(self.buf) > BUFFER_BYTES:
                    del self.buf[:len(self.buf) - BUFFER_BYTES]
                if self.ws is not None:
                    try:
                        await self.ws.send_bytes(bytes(data))
                    except Exception:  # the page went away mid-send: keep the session, detach it
                        self.detach()
        except Exception:
            pass
        await self.end("The session ended.")

    def detach(self) -> None:
        self.ws, self.detached_at = None, time.time()

    async def end(self, why: str = "") -> None:
        if self.ended:
            return
        self.ended = True
        _sessions.pop(self.id, None)
        if self.ws is not None:
            try:
                await self.ws.send_text(json.dumps({"t": "exit", "why": why}))
                await self.ws.close()
            except Exception:
                pass
        if self.conn is not None:
            self.conn.close()
        _audit("terminal closed", self.host, why=why)


_sessions: dict[str, Session] = {}
_reaper: asyncio.Task | None = None


def _audit(action: str, host: dict, **detail) -> None:
    try:
        _m()._log.info("%s: %s (%s@%s:%s) %s", action, host.get("name"), host.get("user"), host.get("host"),
                       host.get("port"), detail or "")
    except Exception:
        pass


async def _reap_loop() -> None:
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for s in list(_sessions.values()):
            away = CLAUDE_DETACHED_S if s.claude_dir is not None else DETACHED_S
            if s.ws is None and s.detached_at and now - s.detached_at > away:
                await s.end(f"Closed: its page was away for {away // 60} minutes.")
            elif now - s.active > IDLE_S:
                await s.end("Closed after 30 minutes without use.")


def _ensure_reaper() -> None:
    global _reaper
    if _reaper is None or _reaper.done():
        _reaper = asyncio.create_task(_reap_loop())


# --- who may use it ------------------------------------------------------------------------------------------------

def _remote(conn) -> bool:
    """Through a proxy (a tunnel) rather than straight from the home network."""
    m = _m()
    peer = conn.client.host if conn.client else ""
    return bool(conn.headers.get("cf-connecting-ip") or conn.headers.get("x-forwarded-for")) and m._proxy_trusted(peer)


def _unlock_token(expiry: int) -> str:
    sig = hmac.new(_m()._session_key(), f"terminal-unlock:{expiry}".encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def _unlocked(conn) -> bool:
    token = conn.cookies.get(UNLOCK_COOKIE) or ""
    expiry, _, sig = token.partition(".")
    return expiry.isdigit() and int(expiry) > time.time() and secrets.compare_digest(_unlock_token(int(expiry)), token)


def _why_not(conn, ws: bool = False) -> str | None:
    """Why this connection can't use the terminal, or None."""
    m = _m()
    role, _ = m._session_identity(conn.cookies.get(m.SESSION_COOKIE))
    if role != "owner":
        return "Only the owner can use the terminal."
    if not enabled():
        return "The terminal is off. Switch it on in Settings > Control Panel."
    if ws and not m._same_origin(conn):
        return "That didn't come from the Control Panel's own page."
    if _remote(conn) and not _unlocked(conn):
        return "unlock"
    return None


# --- routes ---------------------------------------------------------------------------------------------------------

def _refuse(why: str) -> JSONResponse:
    if why == "unlock":
        return JSONResponse({"error": "From outside your home network, enter your password again first.",
                             "unlock": True}, status_code=403)
    return JSONResponse({"error": why}, status_code=403)


@router.get("/api/term/state")
async def state(request: Request):
    m = _m()
    if m._session_identity(request.cookies.get(m.SESSION_COOKIE))[0] != "owner":
        return JSONResponse({"error": "Only the owner can use the terminal."}, status_code=403)
    if not enabled():
        return {"enabled": False}
    why = _why_not(request)
    return {"enabled": True, "locked": why == "unlock", "remote": _remote(request),
            "public_key": public_key() if why is None else None, "hosts": hosts(),
            "suggested": await suggested() if why is None else [],
            "sessions": [s.public() for s in _sessions.values()], "max_sessions": MAX_SESSIONS}


@router.post("/api/term/unlock")
async def unlock(request: Request):
    m = _m()
    if m._session_identity(request.cookies.get(m.SESSION_COOKIE))[0] != "owner":
        return JSONResponse({"error": "Only the owner can use the terminal."}, status_code=403)
    ip, now = m._client_ip(request), time.time()
    if (blocked := m._login_blocked(ip, now)) is not None:
        return blocked
    body = await request.json()
    if not m._owner_ok(str((body or {}).get("password") or "")):
        m._login_failed(ip, now, "password (terminal unlock)")
        return JSONResponse({"error": "That's not the password."}, status_code=403)
    expiry = int(now + UNLOCK_S)
    resp = JSONResponse({"ok": True, "until": expiry})
    resp.set_cookie(UNLOCK_COOKIE, _unlock_token(expiry), max_age=UNLOCK_S, httponly=True, secure=True, samesite="strict")
    return resp


@router.post("/api/term/hosts")
async def add_host(request: Request):
    if why := _why_not(request):
        return _refuse(why)
    try:
        return save_host(await request.json() or {})
    except (ValueError, AttributeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.delete("/api/term/hosts/{hid}")
async def delete_host(hid: str, request: Request):
    if why := _why_not(request):
        return _refuse(why)
    return {"removed": ID_RE.match(hid) is not None and remove_host(hid)}


@router.post("/api/term/hosts/{hid}/{action}")
async def host_action(hid: str, action: str, request: Request):
    """setup: add this install's key with the machine's password (used once, never kept). forget-key: trust the
    machine's new identity on the next connection."""
    if why := _why_not(request):
        return _refuse(why)
    h = _host(hid) if ID_RE.match(hid) else None
    if h is None:
        return JSONResponse({"error": "No such machine."}, status_code=404)
    if action == "forget-key":
        forget_key(h)
        return {"ok": True}
    if action != "setup":
        return JSONResponse({"error": "Not found."}, status_code=404)
    password = str((await request.json() or {}).get("password") or "")
    if not password:
        return JSONResponse({"error": "Type that machine's password (used once to add the key, never kept)."},
                            status_code=400)
    try:
        out = await setup_access(h, password)
    except ConnectionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    finally:
        password = ""
    _audit("terminal access set up", h)
    return out


@router.post("/api/term/sessions/{sid}/close")
async def close_session(sid: str, request: Request):
    if why := _why_not(request):
        return _refuse(why)
    s = _sessions.get(sid) if SID_RE.match(sid) else None
    if s:
        await s.end("Closed.")
    return {"closed": bool(s)}


@router.websocket("/api/term/ws")
async def terminal_ws(ws: WebSocket):
    why = _why_not(ws, ws=True)
    if why:
        await ws.close(code=4403, reason=("unlock" if why == "unlock" else why)[:120])
        return
    q = ws.query_params
    try:
        cols, rows = max(20, min(400, int(q.get("cols") or 80))), max(5, min(200, int(q.get("rows") or 24)))
    except ValueError:
        cols, rows = 80, 24
    await ws.accept()
    _ensure_reaper()
    sid = q.get("sid") or ""
    s = _sessions.get(sid) if SID_RE.match(sid) else None
    if s is None:
        h = _host(q.get("host") or "") if ID_RE.match(q.get("host") or "") else None
        if h is None:
            await ws.send_text(json.dumps({"t": "error", "why": "No such machine."}))
            await ws.close()
            return
        if len(_sessions) >= MAX_SESSIONS:
            await ws.send_text(json.dumps({"t": "error", "why": f"{MAX_SESSIONS} sessions are open: close one first."}))
            await ws.close()
            return
        claude_dir = None
        if q.get("run") == "claude":
            claude_dir = (q.get("dir") or "").strip()
            if claude_dir and not DIR_RE.match(claude_dir):
                await ws.send_text(json.dumps({"t": "error", "why": "The folder is a full path, like /home/me/project "
                                               "or C:\\Code\\project, with no quotes."}))
                await ws.close()
                return
            if claude_dir != h.get("claude_dir", ""):  # remembered for next time
                remember_claude_dir(h["id"], claude_dir)
        s = Session(secrets.token_hex(8), h, claude_dir)
        try:
            await s.start(cols, rows)
        except ConnectionError as exc:
            await ws.send_text(json.dumps({"t": "error", "why": str(exc)}))
            await ws.close()
            return
        _sessions[s.id] = s
        _audit("claude opened" if claude_dir is not None else "terminal opened", h)
    else:  # back after a reload or a dropped connection: the last screen, then live again
        if s.ws is not None:
            try:
                await s.ws.close()
            except Exception:
                pass
        if s.buf:
            await ws.send_bytes(bytes(s.buf))
        try:
            s.proc.change_terminal_size(cols, rows)
        except Exception:
            pass
    s.ws, s.detached_at = ws, None
    await ws.send_text(json.dumps({"t": "ready", "sid": s.id, "name": s.name}))
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            s.active = time.time()
            if msg.get("bytes") is not None:  # keystrokes and pastes
                s.proc.stdin.write(msg["bytes"])
            elif msg.get("text"):
                try:
                    ctl = json.loads(msg["text"])
                except ValueError:
                    continue
                if ctl.get("t") == "resize":
                    try:
                        s.proc.change_terminal_size(max(20, min(400, int(ctl["c"]))), max(5, min(200, int(ctl["r"]))))
                    except (KeyError, TypeError, ValueError):
                        pass
                elif ctl.get("t") == "in" and isinstance(ctl.get("d"), str):
                    s.proc.stdin.write(ctl["d"].encode())
    except (WebSocketDisconnect, RuntimeError):
        pass
    if s.ws is ws:
        s.detach()
