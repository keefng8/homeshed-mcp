"""docker.container.list — the foundation's example capability. See ../../capabilities/docker/list.md."""
from __future__ import annotations

import docker
from docker.errors import ImageNotFound, NotFound
from tools.docker._client import client as docker_client

from registry import tool


@tool(name="container.list", category="docker", doc="docker/list.md")
def list_containers(all: bool = False) -> list[dict]:
    """List Docker containers on this host.

    Args:
        all: include stopped containers, not just running ones.

    Returns:
        One dict per container: id, name, image, status, ports. `image` is `"<unknown>"` if the
        container's own image was since removed (a real failure mode, not hypothetical — found
        live 2026-09-22: one container's stale image reference crashed this capability entirely
        instead of just that one entry, because `c.image` triggers a fresh `inspect_image` call
        per container and any single `ImageNotFound` propagated up and killed the whole list).

    Raises:
        Nothing beyond what `docker.from_env()`/`client.containers.list()` themselves raise —
        a per-container image lookup failure is caught and degrades that one field, not the call.
    """
    client = docker_client()
    try:
        containers = client.containers.list(all=all)
        result = []
        for c in containers:
            try:
                image = c.image.tags[0] if c.image.tags else c.image.short_id
            except (ImageNotFound, NotFound):
                image = "<unknown>"
            result.append(
                {
                    "id": c.short_id,
                    "name": c.name,
                    "image": image,
                    "status": c.status,
                    "ports": c.ports,
                }
            )
        return result
    finally:
        client.close()
