"""docker.container.start. See ../../capabilities/docker/start.md."""
from __future__ import annotations

import docker
from tools.docker._client import client as docker_client

from registry import tool


@tool(name="container.start", category="docker", doc="docker/start.md")
def container_start(container: str, dry_run: bool = True) -> dict:
    """Start a stopped Docker container. WRITE-RISK — same dry-run-by-default pattern as
    docker.container.restart/.stop.

    Defaults to dry_run=True: reports the container's current state and what would happen,
    without touching it. This parameter IS the confirmation step.

    Args:
        container: container name or ID.
        dry_run: if True (default), report only, no side effects. If False, actually start.

    Returns:
        {container, status_before, dry_run, started, status_after}
        status_after is null when dry_run=True (nothing happened).

    Raises:
        docker.errors.NotFound: no container matches `container`. Not caught/wrapped.
    """
    client = docker_client()
    try:
        c = client.containers.get(container)
        status_before = c.status

        if dry_run:
            return {
                "container": c.name,
                "status_before": status_before,
                "dry_run": True,
                "started": False,
                "status_after": None,
            }

        c.start()
        c.reload()
        return {
            "container": c.name,
            "status_before": status_before,
            "dry_run": False,
            "started": True,
            "status_after": c.status,
        }
    finally:
        client.close()
