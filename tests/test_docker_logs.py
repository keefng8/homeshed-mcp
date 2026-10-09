"""Tests for docker.container.logs. Docker SDK is mocked — no live daemon required."""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "docker" and c.name == "container.logs"]
    assert len(matches) == 1


def test_returns_lines():
    from tools.docker import container_logs as module

    fake_container = MagicMock()
    fake_container.name = "my-app"
    fake_container.logs.return_value = b"2026-09-20T00:00:00Z line one\n2026-09-20T00:00:01Z line two\n"

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("tools.docker.container_logs.docker.from_env", return_value=fake_client):
        result = module.container_logs("my-app", tail=50)

    assert result["container"] == "my-app"
    assert result["lines"] == [
        "2026-09-20T00:00:00Z line one",
        "2026-09-20T00:00:01Z line two",
    ]
    fake_container.logs.assert_called_once_with(tail=50, timestamps=True)
    fake_client.close.assert_called_once()


def test_tail_out_of_range_rejected():
    from tools.docker import container_logs as module

    with pytest.raises(ValueError):
        module.container_logs("x", tail=0)
    with pytest.raises(ValueError):
        module.container_logs("x", tail=5000)
