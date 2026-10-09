"""netguard (the network.* SSRF guard, release blocker #5). Drafted by the Github prepper session, 2026-09-29; its
staging-only registry stub is gone here, where the real registry is importable (the stub replaced it for every later
test in the run)."""
import socket

import pytest

import netguard
from tools.network import dns_lookup as dns_mod
from tools.network import port_check as pc_mod

port_check = getattr(pc_mod.port_check, "__wrapped__", pc_mod.port_check)
dns_lookup = getattr(dns_mod.dns_lookup, "__wrapped__", dns_mod.dns_lookup)


def fake_dns(monkeypatch, table):
    """table: hostname -> list of IPs (empty list = resolution failure)."""
    def getaddrinfo(host, *_a, **_k):
        ips = table.get(host)
        if not ips:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]
    monkeypatch.setattr(netguard.socket, "getaddrinfo", getaddrinfo)


@pytest.fixture(autouse=True)
def no_allow_list(monkeypatch):
    monkeypatch.delenv("NETWORK_ALLOWED_CIDRS", raising=False)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.10", "169.254.169.254",   # loopback, RFC1918, metadata
    "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1",    # CGNAT, unspecified, v6 local, mapped
])
def test_non_public_addresses_are_blocked(monkeypatch, ip):
    fake_dns(monkeypatch, {"evil.example": [ip]})
    with pytest.raises(netguard.BlockedHost):
        netguard.resolve_permitted("evil.example")


def test_one_private_address_among_public_ones_blocks_the_name(monkeypatch):
    fake_dns(monkeypatch, {"mixed.example": ["93.184.216.34", "10.0.0.5"]})
    with pytest.raises(netguard.BlockedHost):
        netguard.resolve_permitted("mixed.example")


@pytest.mark.parametrize("host", ["localhost", "db.internal", "printer.local", "LOCALHOST.", "nas.home.arpa"])
def test_internal_names_are_blocked_before_any_lookup(monkeypatch, host):
    fake_dns(monkeypatch, {})  # a lookup would fail differently; the name check must fire first
    with pytest.raises(netguard.BlockedHost, match="internal name"):
        netguard.resolve_permitted(host)


def test_public_host_passes(monkeypatch):
    fake_dns(monkeypatch, {"example.com": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]})
    assert netguard.resolve_permitted("example.com") == ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]


def test_owner_can_allow_their_own_ranges(monkeypatch):
    monkeypatch.setenv("NETWORK_ALLOWED_CIDRS", "192.168.0.0/16")
    fake_dns(monkeypatch, {"nas.lan": ["192.168.1.10"], "other": ["10.0.0.1"]})
    assert netguard.resolve_permitted("nas.lan") == ["192.168.1.10"]
    with pytest.raises(netguard.BlockedHost):
        netguard.resolve_permitted("other")  # only the allowed range, not every private one


def test_a_bad_allow_list_entry_is_an_error_not_ignored(monkeypatch):
    monkeypatch.setenv("NETWORK_ALLOWED_CIDRS", "192.168.0.0/16,not-a-range")
    fake_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    with pytest.raises(netguard.BlockedHost, match="invalid range"):
        netguard.resolve_permitted("example.com")


def test_port_check_connects_to_the_vetted_address_not_the_name(monkeypatch):
    fake_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    seen = {}

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def create_connection(addr, timeout):
        seen["addr"] = addr
        return Conn()
    monkeypatch.setattr(pc_mod.socket, "create_connection", create_connection)
    out = port_check("example.com", 443)
    assert out["open"] is True and seen["addr"] == ("93.184.216.34", 443)


def test_port_check_refuses_a_private_target(monkeypatch):
    fake_dns(monkeypatch, {"sneaky.example": ["127.0.0.1"]})
    monkeypatch.setattr(pc_mod.socket, "create_connection", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(pc_mod.PortCheckError, match="non-public"):
        port_check("sneaky.example", 22)


def test_port_check_unresolvable_name_is_a_normal_result(monkeypatch):
    fake_dns(monkeypatch, {})
    assert port_check("nope.example", 80)["error"] == "name resolution failed"


def test_dns_lookup_refuses_names_that_resolve_privately(monkeypatch):
    fake_dns(monkeypatch, {"intranet.example": ["10.0.0.9"], "example.com": ["93.184.216.34"]})
    with pytest.raises(dns_mod.DNSError):
        dns_lookup("intranet.example")
    assert dns_lookup("example.com") == {"hostname": "example.com",
                                         "addresses": [{"ip": "93.184.216.34", "family": "IPv4"}]}
