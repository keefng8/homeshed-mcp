"""network.exposure_check. See ../../capabilities/network/exposure_check.md.

What your own public sites show the internet on the paths attackers try first: admin panels, secret files, server
status pages (capability list #3, 2026-09-30: R&D found Proxmox, OmniRoute and an old Strapi /admin open by hand).

Read-only and light: a few GETs per host, no logins, no redirects followed, at most MAX_HOSTS x MAX_PATHS requests
a call. Public addresses only (it checks what the internet sees; a private or internal name is skipped), and each
request goes to the address checked, with the site's own TLS name, never a second DNS lookup. A site that answers
every path (a catch-all page) is recognised by a random path first, so its 200s aren't reported as open; a secret file
counts as open only when its content looks like one.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import secrets
import socket
import ssl
from urllib.parse import urlsplit

from registry import tool

DEFAULT_PATHS = ["/admin", "/wp-admin/", "/phpmyadmin/", "/.env", "/.git/HEAD", "/server-status", "/actuator"]
SECRET_BODIES = {"/.env": re.compile(r"(?m)^[A-Z][A-Z0-9_]{2,}=\S"), "/.git/HEAD": re.compile(r"^ref: refs/")}
MAX_HOSTS, MAX_PATHS, READ_BYTES, TIMEOUT = 10, 12, 2048, 8.0
HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


class ExposureError(ValueError):
    """Bad input: no hosts, too many, or a malformed name or path."""


def _public_address(host: str) -> str:
    """The first address of a host whose addresses are all public, or ValueError saying why it's skipped."""
    try:
        addresses = list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(host, None)))
    except socket.gaierror:
        raise ValueError("the name doesn't resolve") from None
    for a in addresses:
        ip = ipaddress.ip_address(a.split("%")[0])
        ip = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
        if not ip.is_global:
            raise ValueError("it resolves to a private address; this checks what the internet sees")
    return addresses[0]


def _get(host: str, port: int, address: str, path: str) -> tuple[int, str, str]:
    """(status, Location header, the first bytes of the body) for one GET over HTTPS to the vetted address."""
    ctx = ssl.create_default_context()

    class _Conn(http.client.HTTPSConnection):
        def connect(self):
            sock = socket.create_connection((address, port), timeout=TIMEOUT)
            self.sock = ctx.wrap_socket(sock, server_hostname=host)

    conn = _Conn(host, port, timeout=TIMEOUT, context=ctx)
    try:
        conn.request("GET", path, headers={"Host": host, "User-Agent": "exposure-check/1.0"})
        r = conn.getresponse()
        return r.status, r.getheader("Location") or "", r.read(READ_BYTES).decode("utf-8", "replace")
    finally:
        conn.close()


def verdict(path: str, status: int, location: str, body: str, catch_all: bool) -> str:
    if 200 <= status < 300:
        if path in SECRET_BODIES:
            return "OPEN: a secret file is readable" if SECRET_BODIES[path].search(body) else "answers, but not with that file"
        return "answers every path (catch-all page)" if catch_all else "OPEN: answers without signing in"
    if status in (301, 302, 303, 307, 308):
        if "cloudflareaccess.com" in location:
            return "protected (Cloudflare Access)"
        return "protected (redirects to a sign-in page)" if re.search(r"(?i)log-?in|sign-?in|auth", location) \
            else f"redirects to {location[:80] or 'somewhere'}"
    if status in (401, 403):
        return "protected"
    if status in (404, 410):
        return "not found"
    return f"HTTP {status}"


def _host_and_port(entry: str) -> tuple[str, int]:
    raw = entry.strip().lower()
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") or parts.username:
        raise ExposureError(f"give a plain hostname (optionally :port), not {entry!r}")
    if not HOST.match(parts.hostname):
        raise ExposureError(f"not a hostname: {entry!r}")
    return parts.hostname, parts.port or 443


@tool(name="exposure_check", category="network", doc="network/exposure_check.md")
def exposure_check(hosts: list[str], paths: list[str] | None = None) -> dict:
    """What your own public sites answer on the paths attackers try first (admin panels, /.env, /.git, status pages).
    Read-only: one GET per path, no logins, no redirects followed. Public hosts only.

    Args:
        hosts: your sites' hostnames, e.g. ["example.com", "admin.example.com:8443"]. At most 10.
        paths: the paths to try (default: /admin, /wp-admin/, /phpmyadmin/, /.env, /.git/HEAD, /server-status,
            /actuator). At most 12. For an admin app that lives at the root of its own name, pass ["/"].

    Returns:
        {"hosts": [{host, checked, open, findings: [{path, status, verdict}]} or {host, skipped}], "open": n}.
        findings leaves out paths that aren't there; "open" counts what answers without signing in.
    """
    hosts = [h for h in (hosts or []) if str(h).strip()]
    if not hosts or len(hosts) > MAX_HOSTS:
        raise ExposureError(f"give 1 to {MAX_HOSTS} hostnames")
    paths = list(paths) if paths else list(DEFAULT_PATHS)
    if len(paths) > MAX_PATHS or not all(isinstance(p, str) and p.startswith("/") and len(p) < 200 for p in paths):
        raise ExposureError(f"give up to {MAX_PATHS} paths, each starting with /")
    out, total_open = [], 0
    for entry in hosts:
        host, port = _host_and_port(str(entry))
        name = f"{host}:{port}" if port != 443 else host
        try:
            address = _public_address(host)
            catch_all = 200 <= _get(host, port, address, f"/exposure-check-{secrets.token_hex(6)}")[0] < 300
        except (ValueError, OSError, http.client.HTTPException) as exc:
            out.append({"host": name, "skipped": str(exc) or type(exc).__name__})
            continue
        findings = []
        for path in paths:
            try:
                status, location, body = _get(host, port, address, path)
            except (OSError, http.client.HTTPException) as exc:
                findings.append({"path": path, "status": None, "verdict": f"no answer ({type(exc).__name__})"})
                continue
            v = verdict(path, status, location, body, catch_all)
            if v != "not found":
                findings.append({"path": path, "status": status, "verdict": v})
        opened = sum(f["verdict"].startswith("OPEN") for f in findings)
        total_open += opened
        out.append({"host": name, "checked": len(paths), "open": opened, "catch_all": catch_all, "findings": findings})
    return {"hosts": out, "open": total_open}
