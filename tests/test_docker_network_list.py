"""Tests for docker.network.list. Docker SDK fully mocked."""
from unittest.mock import MagicMock, patch

from docker.errors import NotFound


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "docker" and c.name == "network.list"]
    assert len(matches) == 1


def test_reloads_each_network_and_extracts_containers():
    from tools.docker import list_networks as module

    fake_net = MagicMock()
    fake_net.short_id = "abc123"
    fake_net.name = "tdai-memory-stack"

    def fake_reload():
        fake_net.attrs = {
            "Driver": "bridge",
            "Scope": "local",
            "Containers": {
                "c1": {"Name": "tdai-memory-core"},
                "c2": {"Name": "tdai-proxy"},
            },
        }

    fake_net.reload.side_effect = fake_reload
    fake_net.attrs = {"Driver": "bridge", "Scope": "local"}  # pre-reload shape, no Containers

    fake_client = MagicMock()
    fake_client.networks.list.return_value = [fake_net]

    with patch("tools.docker.list_networks.docker.from_env", return_value=fake_client):
        result = module.list_networks()

    fake_net.reload.assert_called_once()
    assert result == [
        {
            "id": "abc123",
            "name": "tdai-memory-stack",
            "driver": "bridge",
            "scope": "local",
            "containers": ["tdai-memory-core", "tdai-proxy"],
        }
    ]
    fake_client.close.assert_called_once()


def test_network_with_no_containers():
    from tools.docker import list_networks as module

    fake_net = MagicMock()
    fake_net.short_id = "xyz"
    fake_net.name = "bridge"
    fake_net.attrs = {"Driver": "bridge", "Scope": "local", "Containers": {}}
    fake_net.reload.side_effect = lambda: None

    fake_client = MagicMock()
    fake_client.networks.list.return_value = [fake_net]

    with patch("tools.docker.list_networks.docker.from_env", return_value=fake_client):
        result = module.list_networks()

    assert result[0]["containers"] == []


def test_network_removed_between_list_and_reload_degrades_not_crashes():
    """Proactive fix, 2026-09-22 -- same defensive pattern as docker.container.list/.inspect's
    real ImageNotFound bug, applied here before it was ever observed live (a network removed
    between the initial list and its own reload() call is the same class of narrow race)."""
    from tools.docker import list_networks as module

    healthy = MagicMock()
    healthy.short_id = "abc123"
    healthy.name = "healthy-net"
    healthy.attrs = {"Driver": "bridge", "Scope": "local", "Containers": {}}
    healthy.reload.side_effect = lambda: None

    removed = MagicMock()
    removed.short_id = "def456"
    removed.name = "removed-net"
    removed.attrs = {"Driver": "bridge", "Scope": "local"}
    removed.reload.side_effect = NotFound("network not found")

    fake_client = MagicMock()
    fake_client.networks.list.return_value = [healthy, removed]

    with patch("tools.docker.list_networks.docker.from_env", return_value=fake_client):
        result = module.list_networks()

    assert len(result) == 2
    assert result[0]["containers"] == []
    assert result[1]["name"] == "removed-net"
    assert result[1]["containers"] is None


def test_empty_list():
    from tools.docker import list_networks as module

    fake_client = MagicMock()
    fake_client.networks.list.return_value = []

    with patch("tools.docker.list_networks.docker.from_env", return_value=fake_client):
        result = module.list_networks()

    assert result == []
    fake_client.close.assert_called_once()
