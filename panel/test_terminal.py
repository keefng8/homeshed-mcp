"""The Terminal tab (terminal.py): machines, the install's key, "Set up access" with a password used once, host keys
trusted on first use and refused when they change, sessions that keep their last screen, and who may use it. A real
SSH server (asyncssh) runs in the test on 127.0.0.1; files go under tmp_path."""
import asyncio
import re

import asyncssh
import pytest

import main
import terminal as t


class FakeServer(asyncssh.SSHServer):
    accepted: set = set()

    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "debian" and password == "pw-123"

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        return _bare(key.export_public_key().decode()) in {_bare(k) for k in FakeServer.accepted}


def _bare(pub: str) -> str:
    """A public key without its comment (the server never sees the comment)."""
    return " ".join(pub.split()[:2])


async def _process(proc):
    cmd = proc.command or ""
    if "authorized_keys" in cmd:  # Set up access: take the key the command adds
        m = re.search(r"'(ssh-ed25519 [^']+)'", cmd)
        FakeServer.accepted.add(m.group(1).strip())
        proc.exit(0)
        return
    if cmd == "cat /proc/meminfo":  # the Proxmox tab's real-memory read
        proc.stdout.write("MemTotal:        4000000 kB\nMemFree:  500000 kB\nMemAvailable:    3000000 kB\n")
        proc.exit(0)
        return
    proc.stdout.write("welcome\r\n")
    try:
        async for line in proc.stdin:  # an echoing shell
            proc.stdout.write(f"you said {line.strip()}\r\n")
    except asyncssh.BreakReceived:
        pass
    proc.exit(0)


async def _server(host_key):
    return await asyncssh.create_server(FakeServer, "127.0.0.1", 0, server_host_keys=[host_key],
                                        process_factory=_process)


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_DIR", str(tmp_path))
    FakeServer.accepted = set()
    t._sessions.clear()
    yield tmp_path
    t._sessions.clear()


def _host(port):
    return t.check_host({"name": "Test box", "host": "127.0.0.1", "port": port, "user": "debian"})


def test_machines_are_checked_saved_and_removed(box):
    h = t.save_host({"name": "Test VM", "host": "192.168.1.50", "user": "debian"})
    assert h == {"id": "test-vm", "name": "Test VM", "host": "192.168.1.50", "port": 22, "user": "debian",
                 "found": "manual"}
    for bad in ({"name": "", "host": "x", "user": "root"}, {"name": "A", "host": "bad host;rm", "user": "root"},
                {"name": "A", "host": "10.0.0.1", "user": "Root!"}, {"name": "A", "host": "10.0.0.1", "user": "root",
                                                                      "port": 70000}):
        with pytest.raises(ValueError):
            t.save_host(bad)
    assert t.remove_host("test-vm") and t.hosts() == []


def test_set_up_access_uses_the_password_once_then_the_key_and_remembers_the_machine(box):
    async def run():
        key = asyncssh.generate_private_key("ssh-ed25519")
        server = await _server(key)
        port = server.sockets[0].getsockname()[1]
        h = _host(port)
        with pytest.raises(ConnectionError, match="Set up access"):
            await t.connect(h)  # no key on the machine yet
        with pytest.raises(ConnectionError, match="refused"):
            await t.setup_access(h, "wrong")
        assert await t.setup_access(h, "pw-123") == {"ok": True, "host": "test-box"}
        assert _bare(t.public_key()) in {_bare(k) for k in FakeServer.accepted}
        conn = await t.connect(h)  # the key works now: no password anywhere
        conn.close()
        assert list(t._read(t._known_file(), {})) == [f"127.0.0.1:{port}"]
        assert "pw-123" not in "".join(p.read_text(errors="ignore") for p in box.iterdir() if p.is_file())
        server.close()
        # the same address with a different identity: refused, until the owner forgets the old key
        server = await _server(asyncssh.generate_private_key("ssh-ed25519"))
        h2 = _host(server.sockets[0].getsockname()[1])
        known = t._read(t._known_file(), {})
        known[f"127.0.0.1:{h2['port']}"] = known.pop(f"127.0.0.1:{port}")
        t._write(t._known_file(), known)
        with pytest.raises(ConnectionError, match="identity changed"):
            await t.connect(h2)
        t.forget_key(h2)
        (await t.connect(h2)).close()
        server.close()
    asyncio.run(run())


def test_a_session_keeps_only_its_last_screen_and_ends_cleanly(box, monkeypatch):
    monkeypatch.setattr(t, "BUFFER_BYTES", 64)

    async def run():
        server = await _server(asyncssh.generate_private_key("ssh-ed25519"))
        h = _host(server.sockets[0].getsockname()[1])
        FakeServer.accepted.add(t.public_key())
        s = t.Session("a" * 16, h)
        t._sessions[s.id] = s
        await s.start(80, 24)
        for i in range(10):
            s.proc.stdin.write(f"line {i}\n".encode())
        for _ in range(50):
            if b"line 9" in s.buf:
                break
            await asyncio.sleep(0.05)
        assert b"you said line 9" in s.buf and len(s.buf) <= 64  # only the tail is kept
        await s.end("test")
        assert s.id not in t._sessions
        server.close()
    asyncio.run(run())


def test_a_claude_session_types_cd_and_claude_into_the_shell(box):
    async def run():
        server = await _server(asyncssh.generate_private_key("ssh-ed25519"))
        h = _host(server.sockets[0].getsockname()[1])
        FakeServer.accepted.add(t.public_key())
        s = t.Session("b" * 16, h, "/home/me/my project")
        await s.start(80, 24)
        for _ in range(50):
            if b"you said claude" in s.buf:
                break
            await asyncio.sleep(0.05)
        assert b'you said cd "/home/me/my project"' in s.buf and b"you said claude" in s.buf
        assert s.name == "Claude · Test box" and s.public()["claude"]
        await s.end("test")
        server.close()
    asyncio.run(run())


def test_claude_folders_are_full_paths_without_shell_characters():
    for ok in ("/home/me/project", "E:\\Work", "C:\\Users\\Me\\My stuff (2)", "/srv/app-1.2"):
        assert t.DIR_RE.match(ok), ok
    for bad in ("project", "~/x", '/x"; rm -rf /', "/x$(id)", "/x`id`", "C:\\x & calc", "/a;b", "/a|b", "D:x"):
        assert not t.DIR_RE.match(bad), bad
    assert t.check_host({"name": "Win PC", "host": "10.0.0.9", "user": "Alex"})["user"] == "Alex"


def test_real_memory_comes_from_inside_saved_machines_matched_by_ip(box):
    assert t.parse_meminfo("MemTotal: 100 kB\nMemAvailable: 40 kB\n") == (102400.0, 40960.0)
    assert t.parse_meminfo("MemTotal: 100 kB\n") is None and t.parse_meminfo("garbage") is None
    t._mem.clear()

    async def run():
        server = await _server(asyncssh.generate_private_key("ssh-ed25519"))
        t.save_host({"name": "Test box", "host": "127.0.0.1", "port": server.sockets[0].getsockname()[1],
                     "user": "debian"})
        FakeServer.accepted.add(t.public_key())
        guests = [{"vmid": 100, "status": "running", "ips": ["10.9.9.9", "127.0.0.1"], "mem": 3.9e9, "maxmem": 4.3e9,
                   "mem_basis": "guest"},
                  {"vmid": 101, "status": "running", "ips": ["10.0.0.7"], "mem": 1e9, "mem_basis": "guest"},
                  {"vmid": 102, "status": "stopped", "ips": ["127.0.0.1"], "mem": 0}]
        assert await t.real_memory(guests) == 1
        server.close()
        return guests
    g = asyncio.run(run())
    assert g[0]["mem"] == (4000000 - 3000000) * 1024 and g[0]["mem_total"] == 4000000 * 1024
    assert g[0]["mem_basis"] == "available" and g[1]["mem_basis"] == "guest" and g[2]["mem"] == 0
    t._mem.clear()


class _Conn:
    def __init__(self, cookies=None, headers=None, peer="192.168.1.5"):
        self.cookies, self.headers = cookies or {}, headers or {}
        self.client = type("C", (), {"host": peer})()


def test_only_the_owner_with_the_switch_on_and_unlocked_from_outside(box, monkeypatch):
    monkeypatch.setattr(main, "_session_identity", lambda tok: ("owner", None) if tok == "own" else
                        ("viewer", "friend") if tok == "view" else (None, None))
    on = {"v": False}
    monkeypatch.setattr(main, "panel_value", lambda k: on["v"] if k == t.SETTING else None)
    monkeypatch.setattr(main, "_same_origin", lambda c: c.headers.get("origin") == "https://panel")
    monkeypatch.setattr(main, "_proxy_trusted", lambda peer: True)
    monkeypatch.setattr(main, "_session_key", lambda: b"k" * 32)
    owner = {main.SESSION_COOKIE: "own"}
    assert "off" in t._why_not(_Conn(owner))  # off by default
    on["v"] = True
    assert t._why_not(_Conn({main.SESSION_COOKIE: "view"})) == "Only the owner can use the terminal."
    assert t._why_not(_Conn(owner)) is None  # at home
    assert "own page" in t._why_not(_Conn(owner, {"origin": "https://evil"}), ws=True)
    through = {"origin": "https://panel", "cf-connecting-ip": "8.8.8.8"}
    assert t._why_not(_Conn(owner, through), ws=True) == "unlock"  # through the tunnel: password again
    token = t._unlock_token(int(t.time.time()) + 60)
    assert t._why_not(_Conn({**owner, t.UNLOCK_COOKIE: token}, through), ws=True) is None
    stale = t._unlock_token(int(t.time.time()) - 1)
    assert t._why_not(_Conn({**owner, t.UNLOCK_COOKIE: stale}, through)) == "unlock"
