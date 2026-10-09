"""docker.container.restart. See ../../capabilities/docker/restart.md."""
from __future__ import annotations

import docker
from tools.docker._client import client as docker_client

from registry import tool


@tool(name="container.restart", category="docker", doc="docker/restart.md")
def container_restart(container: str, dry_run: bool = True, timeout: int = 10) -> dict:
    """Restart a Docker container. WRITE-RISK — first write capability in this registry.

    Defaults to dry_run=True: reports the container's current state and what would happen,
    without touching it. This parameter IS the confirmation step — an MCP tool call has no other
    side channel to confirm interactively, so the caller must explicitly opt in with
    dry_run=False before anything actually restarts.

    Args:
        container: container name or ID.
        dry_run: if True (default), report only, no side effects. If False, actually restart.
        timeout: seconds to wait for graceful stop before killing, 0-300. Ignored when
            dry_run=True.

    Returns:
        {container, status_before, dry_run, restarted, status_after}
        status_after is null when dry_run=True (nothing happened).

    Raises:
        docker.errors.NotFound: no container matches `container`. Not caught/wrapped — the
            SDK's own message already names the container.
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
                "restarted": False,
                "status_after": None,
            }

        c.restart(timeout=timeout)
        c.reload()
        return {
            "container": c.name,
            "status_before": status_before,
            "dry_run": False,
            "restarted": True,
            "status_after": c.status,
        }
    finally:
        client.close()
