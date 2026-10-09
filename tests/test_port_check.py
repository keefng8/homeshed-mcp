"""Tests for network.port_check. Real TCP connections against a real local listening socket
and a real closed port -- same reasoning as test_dns_lookup.py: a thin socket wrapper is more
honestly tested against real network behavior than mocked.
"""
import socket
import threading

import pytest


@pytest.fixture(autouse=True)
def allow_loopback(monkeypatch):
    """These tests exercise the tool against real local sockets and names, so they allow loopback the
    way an owner would (NETWORK_ALLOWED_CIDRS); the SSRF guard itself is covered by test_netguard.py."""
    monkeypatch.setenv("NETWORK_ALLOWED_CIDRS", "127.0.0.0/8,::1/128")


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "network" and c.name == "port_check"]
    assert len(matches) == 1


@pytest.fixture
def open_port():
    """A real listening socket on an OS-assigned ephemeral port."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def accept_forever():
        try:
            while True:
                conn, _ = srv.accept()
                conn.close()
        except OSError:
            pass  # socket closed by the test teardown

    t = threading.Thread(target=accept_forever, daemon=True)
    t.start()
    yield port
    srv.close()


def test_open_port_returns_true_with_latency(open_port):
    from tools.network.port_check import port_check

    result = port_check("127.0.0.1", open_port)
    assert result["open"] is True
    assert result["error"] is None
    assert result["latency_ms"] is not None
    assert result["latency_ms"] >= 0


def test_every_vetted_address_is_tried_in_turn(open_port, monkeypatch):
    """Like socket.create_connection with a name: an address that fails (here IPv6 loopback, where nothing listens)
    doesn't hide the one that answers (2026-09-29, merging the SSRF guard)."""
    import netguard
    from tools.network.port_check import port_check

    monkeypatch.setattr(netguard, "resolve_permitted", lambda host: ["::1", "127.0.0.1"])
    result = port_check("dual-stack.example", open_port, timeout=1)
    assert result["open"] is True and result["error"] is None


def test_closed_port_returns_false_not_an_exception():
    from tools.network.port_check import port_check

    # Bind-and-immediately-close to get a real ephemeral port guaranteed to have nothing
    # listening, rather than guessing a "probably free" port number.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()

    result = port_check("127.0.0.1", closed_port, timeout=1.0)
    assert result["open"] is False
    # "connection refused" (Linux, sends RST for a closed port -- the real deploy target's
    # behavior) or "timeout" (Windows dev-machine TCP stack silently drops instead of RST-ing
    # for a closed port with nothing listening -- confirmed live, not assumed) are both a
    # correct "closed" result; which one depends on the OS, not on this code being right or
    # wrong.
    assert result["error"] in ("connection refused", "timeout")
    assert result["latency_ms"] is None


def test_unresolvable_host():
    from tools.network.port_check import port_check

    result = port_check("this-definitely-does-not-exist.invalid", 80, timeout=1.0)
    assert result["open"] is False
    assert result["error"] == "name resolution failed"


def test_rejects_empty_host():
    from tools.network.port_check import PortCheckError, port_check

    with pytest.raises(PortCheckError):
        port_check("", 80)


def test_rejects_out_of_range_port():
    from tools.network.port_check import PortCheckError, port_check

    with pytest.raises(PortCheckError, match="1-65535"):
        port_check("localhost", 0)
    with pytest.raises(PortCheckError, match="1-65535"):
        port_check("localhost", 70000)


def test_rejects_out_of_range_timeout():
    from tools.network.port_check import PortCheckError, port_check

    with pytest.raises(PortCheckError, match="timeout"):
        port_check("localhost", 80, timeout=0)
    with pytest.raises(PortCheckError, match="timeout"):
        port_check("localhost", 80, timeout=100)
