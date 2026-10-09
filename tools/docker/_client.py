"""One place opens the Docker connection, so a missing or unreadable socket gives a plain answer instead of the SDK's
"Error while fetching server API version: ('Connection aborted.', FileNotFoundError(2, ...))" (the prepper's clean-VM
test, 2026-09-30)."""
from __future__ import annotations

NO_SOCKET = ("The Docker socket isn't mounted here, so the docker tools can't reach Docker: "
             "see docs/configuration.md#docker.")
NO_ACCESS = ("The Docker socket is mounted but this server can't read it: add group_add with the socket's group "
             "(docs/configuration.md#docker).")


class DockerUnavailable(RuntimeError):
    """Docker can't be reached from here; the message says how to fix it."""


def client():
    """docker.from_env(), with the two setup mistakes a first-time user makes turned into plain answers."""
    import docker
    from docker.errors import DockerException

    try:
        return docker.from_env()
    except DockerException as exc:
        text = str(exc)
        if "FileNotFoundError" in text or "No such file" in text:
            raise DockerUnavailable(NO_SOCKET) from None
        if "PermissionError" in text or "Permission denied" in text:
            raise DockerUnavailable(NO_ACCESS) from None
        raise
