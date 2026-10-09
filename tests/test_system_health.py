"""Tests for system.health. httpx and the Docker SDK mocked -- no live memory-core/local-AI/
Docker daemon needed. Doesn't test real reachability itself (network.port_check's tests already
cover real socket behavior); tests this capability's own aggregation/error-handling logic.
"""
from unittest.mock import MagicMock, patch

import httpx
import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "system" and c.name == "health"]
    assert len(matches) == 1


@pytest.fixture(autouse=True)
def health_env(monkeypatch):
    monkeypatch.setenv("MEMORY_CORE_BASE_URL", "http://memory-core:8420")
    monkeypatch.setenv("LOCAL_AI_BASE_URL", "http://192.168.1.20:8080/v1")   # the registry's local model:
    monkeypatch.setenv("LOCAL_AI_MODEL", "qwen3-coder-30b")                 # its address and its model's name
    monkeypatch.setenv("MAVIS_STRAPI_BASE_URL", "http://192.168.1.30:1338")
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker.test:2375")   # the SDK itself is mocked in each test


def test_all_healthy():
    from tools.system import health as module

    ok_response = MagicMock(status_code=200)
    fake_docker_client = MagicMock()

    with patch("tools.system.health.httpx.get", return_value=ok_response), \
         patch("tools.system.health.docker.from_env", return_value=fake_docker_client):
        result = module.health()

    assert result["ok"] is True
    assert result["checks"]["memory_core"]["ok"] is True
    assert result["checks"]["local_ai_coder"]["ok"] is True
    assert result["checks"]["mavis_strapi"]["ok"] is True
    assert result["checks"]["docker_daemon"]["ok"] is True
    fake_docker_client.ping.assert_called_once()
    fake_docker_client.close.assert_called_once()


def test_strapi_204_no_content_counts_as_healthy():
    """Strapi's real /_health endpoint returns 204, not 200. A
    health check that only accepted exactly 200 would wrongly report a healthy backend as down."""
    from tools.system import health as module

    no_content_response = MagicMock(status_code=204)
    with patch("tools.system.health.httpx.get", return_value=no_content_response), \
         patch("tools.system.health.docker.from_env", return_value=MagicMock()):
        result = module.health()

    assert result["checks"]["mavis_strapi"]["ok"] is True


def test_mavis_strapi_health_url_uses_underscore_health_path():
    from tools.system import health as module

    ok_response = MagicMock(status_code=200)
    with patch("tools.system.health.httpx.get", return_value=ok_response) as mock_get, \
         patch("tools.system.health.docker.from_env", return_value=MagicMock()):
        module.health()

    urls_checked = [call.args[0] for call in mock_get.call_args_list]
    assert "http://192.168.1.30:1338/_health" in urls_checked


def test_local_ai_health_url_strips_v1_suffix():
    """The configured base URL includes /v1 (OpenAI-compatible path) -- the health check must
    hit the server's own /health, not /v1/health, which doesn't exist on llama.cpp."""
    from tools.system import health as module

    ok_response = MagicMock(status_code=200)
    with patch("tools.system.health.httpx.get", return_value=ok_response) as mock_get, \
         patch("tools.system.health.docker.from_env", return_value=MagicMock()):
        module.health()

    urls_checked = [call.args[0] for call in mock_get.call_args_list]
    assert "http://192.168.1.20:8080/health" in urls_checked


def test_memory_core_unreachable_does_not_crash():
    from tools.system import health as module

    def fake_get(url, timeout):
        if "memory-core" in url:
            raise httpx.ConnectError("connection refused")
        return MagicMock(status_code=200)

    with patch("tools.system.health.httpx.get", side_effect=fake_get), \
         patch("tools.system.health.docker.from_env", return_value=MagicMock()):
        result = module.health()

    assert result["ok"] is False
    assert result["checks"]["memory_core"]["ok"] is False
    assert result["checks"]["memory_core"]["error"] is not None
    assert result["checks"]["local_ai_coder"]["ok"] is True  # one failure doesn't affect the others


def test_docker_daemon_unreachable_does_not_crash():
    from tools.system import health as module

    ok_response = MagicMock(status_code=200)
    with patch("tools.system.health.httpx.get", return_value=ok_response), \
         patch("tools.system.health.docker.from_env", side_effect=Exception("no such file or directory")):
        result = module.health()

    assert result["ok"] is False
    assert result["checks"]["docker_daemon"]["ok"] is False


def test_unconfigured_backends_are_not_checked(monkeypatch):
    """A fresh install sets none of these: its health must be green, not red for services it never asked for."""
    for var in ("MEMORY_CORE_BASE_URL", "LOCAL_AI_BASE_URL", "MAVIS_STRAPI_BASE_URL", "DOCKER_HOST"):
        monkeypatch.delenv(var, raising=False)
    from tools.system import health as module
    monkeypatch.setattr(module, "DOCKER_SOCKETS", ())   # no Docker on this machine

    with patch("tools.system.health.httpx.get") as mock_get, \
         patch("tools.system.health.docker.from_env") as mock_docker:
        result = module.health()

    assert result == {"ok": True, "checks": {}}
    mock_get.assert_not_called()
    mock_docker.assert_not_called()


def test_docker_is_checked_when_its_socket_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    from tools.system import health as module
    sock = tmp_path / "docker.sock"
    sock.touch()
    monkeypatch.setattr(module, "DOCKER_SOCKETS", (str(sock),))

    with patch("tools.system.health.httpx.get", return_value=MagicMock(status_code=200)), \
         patch("tools.system.health.docker.from_env", side_effect=Exception("daemon not running")):
        result = module.health()

    assert result["checks"]["docker_daemon"]["ok"] is False and result["ok"] is False


def test_non_200_status_is_reported():
    from tools.system import health as module

    bad_response = MagicMock(status_code=503)
    with patch("tools.system.health.httpx.get", return_value=bad_response), \
         patch("tools.system.health.docker.from_env", return_value=MagicMock()):
        result = module.health()

    assert result["checks"]["memory_core"]["ok"] is False
    assert result["checks"]["memory_core"]["error"] == "http 503"


def test_no_local_model_means_no_local_check_not_a_failure(monkeypatch):
    """A machine without a GPU (no local backend configured) must not show a permanent red health check."""
    from tools.system import health as module
    monkeypatch.delenv("LOCAL_AI_BASE_URL", raising=False)
    ok_response = MagicMock(status_code=200)
    with patch("tools.system.health.httpx.get", return_value=ok_response), \
         patch("tools.system.health.docker.from_env", return_value=MagicMock()):
        result = module.health()
    assert "local_ai_coder" not in result["checks"] and result["ok"] is True
