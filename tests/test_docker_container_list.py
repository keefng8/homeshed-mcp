"""Tests for docker.container.list. Docker SDK fully mocked -- no live Docker daemon needed."""
from unittest.mock import MagicMock, patch

from docker.errors import ImageNotFound


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "docker" and c.name == "container.list"]
    assert len(matches) == 1


def _fake_container(name, image_tags=None, status="running", ports=None):
    c = MagicMock()
    c.short_id = f"{name}-id"
    c.name = name
    c.status = status
    c.ports = ports or {}
    if image_tags is None:
        # Accessing .image itself raises -- matches the real failure mode (docker-py's `image`
        # property does an inspect_image() call under the hood, not a cached attribute).
        type(c).image = property(lambda self: (_ for _ in ()).throw(ImageNotFound("no such image")))
    else:
        c.image.tags = image_tags
        c.image.short_id = f"{name}-image-id"
    return c


def test_lists_containers_with_real_images():
    from tools.docker import list_containers as module

    fake_client = MagicMock()
    fake_client.containers.list.return_value = [
        _fake_container("mcp-server", image_tags=["mcp-server-mcp-server:latest"]),
        _fake_container("dashboard", image_tags=["dashboard-dashboard:latest"]),
    ]
    with patch("tools.docker.list_containers.docker.from_env", return_value=fake_client):
        result = module.list_containers()

    assert len(result) == 2
    assert result[0]["image"] == "mcp-server-mcp-server:latest"
    assert result[1]["image"] == "dashboard-dashboard:latest"


def test_one_stale_image_does_not_crash_the_whole_list():
    """Real bug, found live 2026-09-22: one container with a since-removed image used to raise
    ImageNotFound and kill the entire call, not just that one entry."""
    from tools.docker import list_containers as module

    fake_client = MagicMock()
    fake_client.containers.list.return_value = [
        _fake_container("healthy", image_tags=["real-image:latest"]),
        _fake_container("stale-image-container", image_tags=None),
    ]
    with patch("tools.docker.list_containers.docker.from_env", return_value=fake_client):
        result = module.list_containers()

    assert len(result) == 2
    assert result[0]["image"] == "real-image:latest"
    assert result[1]["image"] == "<unknown>"
    assert result[1]["name"] == "stale-image-container"


def test_falls_back_to_short_id_when_no_tags():
    from tools.docker import list_containers as module

    fake_client = MagicMock()
    fake_client.containers.list.return_value = [_fake_container("untagged", image_tags=[])]
    with patch("tools.docker.list_containers.docker.from_env", return_value=fake_client):
        result = module.list_containers()

    assert result[0]["image"] == "untagged-image-id"


def test_client_closed_after_use():
    from tools.docker import list_containers as module

    fake_client = MagicMock()
    fake_client.containers.list.return_value = []
    with patch("tools.docker.list_containers.docker.from_env", return_value=fake_client):
        module.list_containers()

    fake_client.close.assert_called_once()


def test_all_flag_passed_through():
    from tools.docker import list_containers as module

    fake_client = MagicMock()
    fake_client.containers.list.return_value = []
    with patch("tools.docker.list_containers.docker.from_env", return_value=fake_client):
        module.list_containers(all=True)

    fake_client.containers.list.assert_called_once_with(all=True)
