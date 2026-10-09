"""docker.container.stop. See ../../capabilities/docker/stop.md."""
from __future__ import annotations

import docker
from tools.docker._client import client as docker_client

from registry import tool


@tool(name="container.stop", category="docker", doc="docker/stop.md")
def container_stop(container: str, dry_run: bool = True, timeout: int = 10) -> dict:
    """Stop a Docker container. WRITE-RISK — same dry-run-by-default pattern as
    docker.container.restart.

    Defaults to dry_run=True: reports the container's current state and what would happen,
    without touching it. This parameter IS the confirmation step.

    Args:
        container: container name or ID.
        dry_run: if True (default), report only, no side effects. If False, actually stop.
        timeout: seconds to wait for graceful stop before killing, 0-300. Ignored when
            dry_run=True.

    Returns:
        {container, status_before, dry_run, stopped, status_after}
        status_after is null when dry_run=True (nothing happened).

    Raises:
        docker.errors.NotFound: no container matches `container`. Not caught/wrapped.
        ValueError: timeout out of range.
    """
    if not 0 <= timeout <= 300:
        raise ValueError("timeout must be between 0 and 300 seconds")

    client = docker_client()
    try:
        c = client.containers.get(container)
        status_before = c.status

        if dry_run:
            return {
                "container": c.name,
                "status_before": status_before,
                "dry_run": True,
                "stopped": False,
                "status_after": None,
            }

        c.stop(timeout=timeout)
        c.reload()
        return {
            "container": c.name,
            "status_before": status_before,
            "dry_run": False,
            "stopped": True,
            "status_after": c.status,
        }
    finally:
        client.close()
