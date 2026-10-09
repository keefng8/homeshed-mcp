"""Tests for network.dns.lookup. Resolves real hostnames (localhost, a deliberately invalid
name) rather than mocking socket.getaddrinfo — this is a thin stdlib wrapper, and a real
resolution is more honest evidence it works than a mock of the exact function under test.
"""
import socket

import pytest


@pytest.fixture(autouse=True)
def allow_loopback(monkeypatch):
    """These tests exercise the tool against real local sockets and names, so they allow loopback the
    way an owner would (NETWORK_ALLOWED_CIDRS); the SSRF guard itself is covered by test_netguard.py."""
    monkeypatch.setenv("NETWORK_ALLOWED_CIDRS", "127.0.0.0/8,::1/128")


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "network" and c.name == "dns.lookup"]
    assert len(matches) == 1


def test_resolves_localhost():
    from tools.network.dns_lookup import dns_lookup

    result = dns_lookup("localhost")
    assert result["hostname"] == "localhost"
    assert len(result["addresses"]) >= 1
    assert all(a["family"] in ("IPv4", "IPv6") for a in result["addresses"])
    assert all(a["ip"] for a in result["addresses"])


def test_no_duplicate_addresses():
    from tools.network.dns_lookup import dns_lookup

    result = dns_lookup("localhost")
    ips = [a["ip"] for a in result["addresses"]]
    assert len(ips) == len(set(ips))


def test_empty_hostname_rejected():
    from tools.network.dns_lookup import DNSError, dns_lookup

    with pytest.raises(DNSError):
        dns_lookup("")
    with pytest.raises(DNSError):
        dns_lookup("   ")


def test_unresolvable_hostname():
    from tools.network.dns_lookup import DNSError, dns_lookup

    with pytest.raises(DNSError, match="could not resolve"):
        dns_lookup("this-definitely-does-not-exist.invalid")
