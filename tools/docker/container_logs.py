"""docker.container.logs. See ../../capabilities/docker/logs.md."""
from __future__ import annotations

import docker
from tools.docker._client import client as docker_client

from registry import tool


@tool(name="container.logs", category="docker", doc="docker/logs.md")
def container_logs(container: str, tail: int = 200) -> dict:
    """Fetch recent log lines from a container.

    Args:
        container: container name or ID.
        tail: number of most recent lines to return, 1-2000.

    Returns:
        {container, lines: [str]}
    """
    if not 1 <= tail <= 2000:
        raise ValueError("tail must be between 1 and 2000")

    client = docker_client()
    try:
        c = client.containers.get(container)
        raw = c.logs(tail=tail, timestamps=True)
        lines = raw.decode("utf-8", errors="replace").splitlines()
        return {"container": c.name, "lines": lines}
    finally:
        client.close()
