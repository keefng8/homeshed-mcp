"""Tests for docker.volume.list. Docker SDK fully mocked."""
from unittest.mock import MagicMock, patch


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "docker" and c.name == "volume.list"]
    assert len(matches) == 1


def test_returns_volume_fields():
    from tools.docker import list_volumes as module

    fake_volume = MagicMock()
    fake_volume.name = "tdai-memory-core-data"
    fake_volume.attrs = {
        "Driver": "local",
        "Mountpoint": "/var/lib/docker/volumes/tdai-memory-core-data/_data",
        "CreatedAt": "2026-09-21T18:20:00Z",
    }

    fake_client = MagicMock()
    fake_client.volumes.list.return_value = [fake_volume]

    with patch("tools.docker.list_volumes.docker.from_env", return_value=fake_client):
        result = module.list_volumes()

    assert result == [
        {
            "name": "tdai-memory-core-data",
            "driver": "local",
            "mountpoint": "/var/lib/docker/volumes/tdai-memory-core-data/_data",
            "created_at": "2026-09-21T18:20:00Z",
        }
    ]
    fake_client.close.assert_called_once()


def test_empty_list():
    from tools.docker import list_volumes as module

    fake_client = MagicMock()
    fake_client.volumes.list.return_value = []

    with patch("tools.docker.list_volumes.docker.from_env", return_value=fake_client):
        result = module.list_volumes()

    assert result == []
    fake_client.close.assert_called_once()
