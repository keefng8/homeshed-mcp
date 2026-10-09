"""tools/docker/_client.py: a missing or unreadable Docker socket gives a plain answer. Drafted by local_ai.ask
(Qwen3-Coder-30B), checked and tightened by hand."""
import docker
import pytest
from docker.errors import DockerException

from tools.docker import _client


def _fails_with(monkeypatch, message):
    def from_env():
        raise DockerException(message)
    monkeypatch.setattr(docker, "from_env", from_env)


def test_no_socket_says_so_and_where_to_read(monkeypatch):
    _fails_with(monkeypatch, "Error while fetching server API version: ('Connection aborted.', "
                             "FileNotFoundError(2, 'No such file or directory'))")
    with pytest.raises(_client.DockerUnavailable) as exc:
        _client.client()
    assert str(exc.value) == _client.NO_SOCKET and "docs/configuration.md#docker" in str(exc.value)


def test_a_socket_it_cannot_read_says_group_add(monkeypatch):
    _fails_with(monkeypatch, "Error while fetching server API version: ('Connection aborted.', "
                             "PermissionError(13, 'Permission denied'))")
    with pytest.raises(_client.DockerUnavailable) as exc:
        _client.client()
    assert str(exc.value) == _client.NO_ACCESS and "group_add" in str(exc.value)


def test_any_other_docker_error_is_left_alone(monkeypatch):
    _fails_with(monkeypatch, "boom")
    with pytest.raises(DockerException, match="boom"):
        _client.client()
