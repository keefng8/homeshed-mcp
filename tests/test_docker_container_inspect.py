"""Tests for docker.container.inspect. Docker SDK fully mocked -- no live daemon needed. First
test coverage for this capability (previously verified only by manual checks, per
ARCHITECTURE.md)."""
from unittest.mock import MagicMock, patch

from docker.errors import ImageNotFound


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "docker" and c.name == "container.inspect"]
    assert len(matches) == 1


def _fake_container(name="test-container", image_tags=("test-image:latest",), env_list=None):
    c = MagicMock()
    c.short_id = "abc123"
    c.name = name
    c.status = "running"
    c.ports = {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}
    c.attrs = {
        "Config": {"Env": env_list or ["PATH=/usr/bin", "SECRET_KEY=hunter2"]},
        "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.2", "Gateway": "172.17.0.1"}}},
        "Mounts": [{"Source": "/host/path", "Destination": "/container/path", "Mode": "rw"}],
    }
    if image_tags is None:
        type(c).image = property(lambda self: (_ for _ in ()).throw(ImageNotFound("no such image")))
    else:
        c.image.tags = list(image_tags)
        c.image.short_id = "image-id-123"
    return c


def test_inspect_returns_all_fields():
    from tools.docker import container_inspect as module

    fake_client = MagicMock()
    fake_client.containers.get.return_value = _fake_container()
    with patch("tools.docker.container_inspect.docker.from_env", return_value=fake_client):
        result = module.container_inspect("test-container")

    assert result["id"] == "abc123"
    assert result["name"] == "test-container"
    assert result["image"] == "test-image:latest"
    assert result["status"] == "running"
    assert result["networks"] == {"bridge": {"ip": "172.17.0.2", "gateway": "172.17.0.1"}}
    assert result["mounts"] == [{"source": "/host/path", "destination": "/container/path", "mode": "rw"}]


def test_env_values_withheld_by_default():
    from tools.docker import container_inspect as module

    fake_client = MagicMock()
    fake_client.containers.get.return_value = _fake_container()
    with patch("tools.docker.container_inspect.docker.from_env", return_value=fake_client):
        result = module.container_inspect("test-container")

    assert result["env"] == ["PATH", "SECRET_KEY"]
    assert "hunter2" not in str(result["env"])


def test_env_values_included_when_requested():
    from tools.docker import container_inspect as module

    fake_client = MagicMock()
    fake_client.containers.get.return_value = _fake_container()
    with patch("tools.docker.container_inspect.docker.from_env", return_value=fake_client):
        result = module.container_inspect("test-container", include_env_values=True)

    assert "SECRET_KEY=hunter2" in result["env"]


def test_stale_image_degrades_to_unknown_not_a_crash():
    """Real bug, found live 2026-09-22 (same root cause as docker.container.list's): a
    container whose image was since removed used to raise ImageNotFound and kill the whole call."""
    from tools.docker import container_inspect as module

    fake_client = MagicMock()
    fake_client.containers.get.return_value = _fake_container(image_tags=None)
    with patch("tools.docker.container_inspect.docker.from_env", return_value=fake_client):
        result = module.container_inspect("test-container")

    assert result["image"] == "<unknown>"


def test_client_closed_after_use():
    from tools.docker import container_inspect as module

    fake_client = MagicMock()
    fake_client.containers.get.return_value = _fake_container()
    with patch("tools.docker.container_inspect.docker.from_env", return_value=fake_client):
        module.container_inspect("test-container")

    fake_client.close.assert_called_once()
