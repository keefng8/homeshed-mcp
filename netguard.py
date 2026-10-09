"""SSRF guard for tools that connect from this server (network.port_check) or reveal internal DNS
(network.dns.lookup). Release blocker #5 (public-release-security-review.md).

Public hosts only by default: every address a name resolves to must be globally routable, and callers
connect to the vetted address itself (never re-resolve, which would reopen DNS rebinding). Owners who want
LAN checks allow their own ranges explicitly:

    NETWORK_ALLOWED_CIDRS=192.168.0.0/16,10.0.0.0/8

Drafted by the Github prepper session, 2026-09-29. web.read keeps its own guard (it never connects to the target
itself: Jina does); its blocked-name lists are duplicated here and could be shared.
"""
from __future__ import annotations

import ipaddress
import os
import socket

_BLOCKED_HOSTS = {
    "home.arpa", "instance-data", "internal", "ip6-localhost", "ip6-loopback",
    "lan", "local", "localdomain", "localhost", "metadata.google.internal",
}
_BLOCKED_SUFFIXES = (".home.arpa", ".internal", ".lan", ".local", ".localdomain", ".localhost")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class BlockedHost(ValueError):
    """The host is (or resolves to) a private, loopback, link-local or otherwise non-public address."""


def allowed_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Ranges the owner explicitly allows, from NETWORK_ALLOWED_CIDRS. Read on every call. A bad entry is an
    error, never silently ignored: a typo must not quietly widen or narrow access."""
    raw = os.environ.get("NETWORK_ALLOWED_CIDRS", "")
    nets = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            raise BlockedHost(f"NETWORK_ALLOWED_CIDRS has an invalid range: {part!r}") from None
    return nets


def _effective(ip: IPAddress) -> IPAddress:
    """An IPv4-mapped IPv6 address (::ffff:127.0.0.1) is judged as the IPv4 address it carries."""
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


def is_permitted(ip: IPAddress) -> bool:
    ip = _effective(ip)
    return ip.is_global or any(ip in net for net in allowed_networks())


def resolve_permitted(host: str) -> list[str]:
    """Resolve host and return its addresses, or raise BlockedHost if the name is internal or ANY address
    isn't permitted. Callers connect to one of the returned addresses, not to the name."""
    name = (host or "").strip().lower().rstrip(".")
    if not name or any(c.isspace() for c in name) or "%" in name:
        raise BlockedHost("host must be a plain hostname or IP address")
    allowed = allowed_networks()
    if (name in _BLOCKED_HOSTS or name.endswith(_BLOCKED_SUFFIXES)) and not allowed:
        raise BlockedHost(f"{host} is an internal name; only public hosts are allowed "
                          "(set NETWORK_ALLOWED_CIDRS to allow your own ranges)")
    try:
        infos = socket.getaddrinfo(name, None)
    except socket.gaierror as e:
        raise BlockedHost(f"could not resolve {host}: {e.strerror}") from None
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise BlockedHost(f"could not resolve {host}")
    for addr in addresses:
        if not is_permitted(ipaddress.ip_address(addr.split("%")[0])):
            raise BlockedHost(f"{host} resolves to a non-public address; only public hosts are allowed "
                              "(set NETWORK_ALLOWED_CIDRS to allow your own ranges)")
    return addresses
