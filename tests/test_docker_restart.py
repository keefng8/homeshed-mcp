"""Tests for docker.container.restart. Docker SDK is fully mocked — this is the write-risk
capability, it must never touch a real container in a test run. The single most important
property tested here: dry_run=True (the default) never calls the SDK's restart().
"""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "docker" and c.name == "container.restart"]
    assert len(matches) == 1


def test_dry_run_default_never_calls_restart():
    from tools.docker import container_restart as module

    fake_container = MagicMock()
    fake_container.name = "my-app"
    fake_container.status = "running"

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_restart.docker.from_env", return_value=fake_client):
        result = module.container_restart("my-app")

    assert result == {
        "container": "my-app",
        "status_before": "running",
        "dry_run": True,
        "restarted": False,
        "status_after": None,
    }
    fake_container.restart.assert_not_called()
    fake_client.close.assert_called_once()


def test_explicit_dry_run_true_never_calls_restart():
    from tools.docker import container_restart as module

    fake_container = MagicMock()
    fake_container.name = "x"
    fake_container.status = "exited"
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_restart.docker.from_env", return_value=fake_client):
        module.container_restart("x", dry_run=True)

    fake_container.restart.assert_not_called()


def test_dry_run_false_actually_restarts():
    from tools.docker import container_restart as module

    fake_container = MagicMock()
    fake_container.name = "my-app"
    fake_container.status = "running"

    def fake_reload():
        fake_container.status = "running"  # simulates post-restart state

    fake_container.reload.side_effect = fake_reload

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_restart.docker.from_env", return_value=fake_client):
        result = module.container_restart("my-app", dry_run=False, timeout=5)

    fake_container.restart.assert_called_once_with(timeout=5)
    fake_container.reload.assert_called_once()
    assert result["dry_run"] is False
    assert result["restarted"] is True
    assert result["status_after"] == "running"


def test_default_timeout_used():
    from tools.docker import container_restart as module

    fake_container = MagicMock()
    fake_container.name = "x"
    fake_container.status = "running"
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_restart.docker.from_env", return_value=fake_client):
        module.container_restart("x", dry_run=False)

    fake_container.restart.assert_called_once_with(timeout=10)


def test_invalid_timeout_rejected_before_touching_docker():
    from tools.docker import container_restart as module

    with patch("tools.docker.container_restart.docker.from_env") as mock_from_env:
        with pytest.raises(ValueError):
            module.container_restart("x", timeout=-1)
        with pytest.raises(ValueError):
            module.container_restart("x", timeout=301)
    mock_from_env.assert_not_called()


def test_container_not_found_propagates():
    from tools.docker import container_restart as module
    import docker.errors

    fake_client = MagicMock()
    fake_client.containers.get.side_effect = docker.errors.NotFound("no such container")

    with patch("tools.docker.container_restart.docker.from_env", return_value=fake_client):
        with pytest.raises(docker.errors.NotFound):
            module.container_restart("does-not-exist")

    fake_client.close.assert_called_once()
