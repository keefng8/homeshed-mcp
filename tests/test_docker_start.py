"""Tests for docker.container.start. Docker SDK fully mocked — write-risk capability, must never
touch a real container in a test run.
"""
from unittest.mock import MagicMock, patch


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "docker" and c.name == "container.start"]
    assert len(matches) == 1


def test_dry_run_default_never_calls_start():
    from tools.docker import container_start as module

    fake_container = MagicMock()
    fake_container.name = "my-app"
    fake_container.status = "exited"

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_start.docker.from_env", return_value=fake_client):
        result = module.container_start("my-app")

    assert result == {
        "container": "my-app",
        "status_before": "exited",
        "dry_run": True,
        "started": False,
        "status_after": None,
    }
    fake_container.start.assert_not_called()
    fake_client.close.assert_called_once()


def test_dry_run_false_actually_starts():
    from tools.docker import container_start as module

    fake_container = MagicMock()
    fake_container.name = "my-app"
    fake_container.status = "exited"

    def fake_reload():
        fake_container.status = "running"

    fake_container.reload.side_effect = fake_reload

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_start.docker.from_env", return_value=fake_client):
        result = module.container_start("my-app", dry_run=False)

    fake_container.start.assert_called_once_with()
    fake_container.reload.assert_called_once()
    assert result["dry_run"] is False
    assert result["started"] is True
    assert result["status_after"] == "running"


def test_container_not_found_propagates():
    from tools.docker import container_start as module
    import docker.errors

    fake_client = MagicMock()
    fake_client.containers.get.side_effect = docker.errors.NotFound("no such container")

    with patch("tools.docker.container_start.docker.from_env", return_value=fake_client):
        import pytest

        with pytest.raises(docker.errors.NotFound):
            module.container_start("does-not-exist")

    fake_client.close.assert_called_once()
