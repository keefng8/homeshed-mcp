"""network.port_check. See ../../capabilities/network/port-check.md."""
from __future__ import annotations

import socket
import time

import netguard
from registry import tool


class PortCheckError(RuntimeError):
    """Bad host/port input, or a non-public host — not a connectivity failure, which is a normal result."""


@tool(name="port_check", category="network", doc="network/port-check.md")
def port_check(host: str, port: int, timeout: float = 3.0) -> dict:
    """Test TCP connectivity to a host:port: a real connection attempt, not a guess.

    Public hosts only unless the owner allows ranges with NETWORK_ALLOWED_CIDRS (netguard.py). The name is
    resolved and vetted once, and the connection goes to that vetted address (no second lookup, so DNS
    rebinding can't swap in a private address).

    Args:
        host: hostname or IP to connect to.
        port: TCP port, 1-65535.
        timeout: seconds to wait for the connection, 0.1-30.

    Returns:
        {host, port, open: bool, latency_ms: float | None, error: str | None}. A closed or unreachable port is
        a normal result, not an exception.

    Raises:
        PortCheckError: empty host, port/timeout out of range, or a host that isn't permitted.
    """
    if not host or not host.strip():
        raise PortCheckError("host must be non-empty")
    if not 1 <= port <= 65535:
        raise PortCheckError(f"port must be 1-65535, got {port}")
    if not 0.1 <= timeout <= 30:
        raise PortCheckError(f"timeout must be 0.1-30 seconds, got {timeout}")
    try:
        addresses = netguard.resolve_permitted(host)
    except netguard.BlockedHost as e:
        if str(e).startswith("could not resolve"):
            return {"host": host, "port": port, "open": False, "latency_ms": None, "error": "name resolution failed"}
        raise PortCheckError(str(e)) from None

    # Every vetted address in turn, as socket.create_connection does with a name: a host whose first answer is an
    # IPv6 address this server can't route to is still found open on its IPv4 one.
    error = "unreachable"
    for address in addresses:
        start = time.monotonic()
        try:
            with socket.create_connection((address, port), timeout=timeout):
                latency_ms = round((time.monotonic() - start) * 1000, 1)
                return {"host": host, "port": port, "open": True, "latency_ms": latency_ms, "error": None}
        except socket.timeout:
            error = "timeout"
        except ConnectionRefusedError:
            error = "connection refused"
        except OSError as e:
            error = str(e)
    return {"host": host, "port": port, "open": False, "latency_ms": None, "error": error}
