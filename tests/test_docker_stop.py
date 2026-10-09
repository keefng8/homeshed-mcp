"""Tests for docker.container.stop. Docker SDK fully mocked — write-risk capability, must never
touch a real container in a test run. Same shape as test_docker_restart.py.
"""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "docker" and c.name == "container.stop"]
    assert len(matches) == 1


def test_dry_run_default_never_calls_stop():
    from tools.docker import container_stop as module

    fake_container = MagicMock()
    fake_container.name = "my-app"
    fake_container.status = "running"

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_stop.docker.from_env", return_value=fake_client):
        result = module.container_stop("my-app")

    assert result == {
        "container": "my-app",
        "status_before": "running",
        "dry_run": True,
        "stopped": False,
        "status_after": None,
    }
    fake_container.stop.assert_not_called()
    fake_client.close.assert_called_once()


def test_dry_run_false_actually_stops():
    from tools.docker import container_stop as module

    fake_container = MagicMock()
    fake_container.name = "my-app"
    fake_container.status = "running"

    def fake_reload():
        fake_container.status = "exited"

    fake_container.reload.side_effect = fake_reload

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_stop.docker.from_env", return_value=fake_client):
        result = module.container_stop("my-app", dry_run=False, timeout=5)

    fake_container.stop.assert_called_once_with(timeout=5)
    fake_container.reload.assert_called_once()
    assert result["dry_run"] is False
    assert result["stopped"] is True
    assert result["status_after"] == "exited"


def test_default_timeout_used():
    from tools.docker import container_stop as module

    fake_container = MagicMock()
    fake_container.name = "x"
    fake_container.status = "running"
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_stop.docker.from_env", return_value=fake_client):
        module.container_stop("x", dry_run=False)

    fake_container.stop.assert_called_once_with(timeout=10)


def test_invalid_timeout_rejected_before_touching_docker():
    from tools.docker import container_stop as module

    with patch("tools.docker.container_stop.docker.from_env") as mock_from_env:
        with pytest.raises(ValueError):
            module.container_stop("x", timeout=-1)
        with pytest.raises(ValueError):
            module.container_stop("x", timeout=301)
    mock_from_env.assert_not_called()


def test_container_not_found_propagates():
    from tools.docker import container_stop as module
    import docker.errors

    fake_client = MagicMock()
    fake_client.containers.get.side_effect = docker.errors.NotFound("no such container")

    with patch("tools.docker.container_stop.docker.from_env", return_value=fake_client):
        with pytest.raises(docker.errors.NotFound):
            module.container_stop("does-not-exist")

    fake_client.close.assert_called_once()
