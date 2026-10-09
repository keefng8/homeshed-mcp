"""network.dns.lookup. See ../../capabilities/network/dns-lookup.md."""
from __future__ import annotations

import socket

import netguard
from registry import tool


class DNSError(RuntimeError):
    """Any DNS resolution failure, or a name that resolves to a non-public address."""


@tool(name="dns.lookup", category="network", doc="network/dns-lookup.md")
def dns_lookup(hostname: str) -> dict:
    """Resolve a hostname to its IP addresses (A/AAAA only). stdlib-only, no external DNS library.

    Names that resolve to private, loopback or link-local addresses are refused unless the owner allows
    those ranges with NETWORK_ALLOWED_CIDRS (netguard.py), so the tool can't be used to map an internal
    network.

    Args:
        hostname: the hostname to resolve.

    Returns:
        {hostname, addresses: [{ip, family}]}

    Raises:
        DNSError: the name doesn't resolve, or resolves to an address that isn't permitted.
    """
    if not hostname or not hostname.strip():
        raise DNSError("hostname must be non-empty")
    try:
        addresses = netguard.resolve_permitted(hostname)
    except netguard.BlockedHost as e:
        raise DNSError(str(e)) from None
    return {"hostname": hostname.strip(),
            "addresses": [{"ip": ip, "family": "IPv6" if ":" in ip else "IPv4"} for ip in addresses]}
