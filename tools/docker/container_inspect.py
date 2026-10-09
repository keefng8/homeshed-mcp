"""docker.container.inspect. See ../../capabilities/docker/inspect.md."""
from __future__ import annotations

import docker
from docker.errors import ImageNotFound, NotFound
from tools.docker._client import client as docker_client

from registry import tool


@tool(name="container.inspect", category="docker", doc="docker/inspect.md")
def container_inspect(container: str, include_env_values: bool = False) -> dict:
    """Inspect a single container's config, networking and mounts.

    Args:
        container: container name or ID.
        include_env_values: include env var values, not just names. Off by default —
            env vars commonly hold secrets (DB passwords, API keys).

    Returns:
        {id, name, image, status, networks, ports, mounts, env}. `image` is `"<unknown>"` if the
        container's own image was since removed — same real failure mode as
        `docker.container.list` (2026-09-22), fixed here too rather than left for whoever hits
        it next.
    """
    client = docker_client()
    try:
        c = client.containers.get(container)
        attrs = c.attrs
        env_list = attrs.get("Config", {}).get("Env") or []
        env = env_list if include_env_values else [e.split("=", 1)[0] for e in env_list]
        networks = {
            name: {"ip": net.get("IPAddress"), "gateway": net.get("Gateway")}
            for name, net in attrs.get("NetworkSettings", {}).get("Networks", {}).items()
        }
        mounts = [
            {"source": m.get("Source"), "destination": m.get("Destination"), "mode": m.get("Mode")}
            for m in attrs.get("Mounts", [])
        ]
        try:
            image = c.image.tags[0] if c.image.tags else c.image.short_id
        except (ImageNotFound, NotFound):
            image = "<unknown>"
        return {
            "id": c.short_id,
            "name": c.name,
            "image": image,
            "status": c.status,
            "networks": networks,
            "ports": c.ports,
            "mounts": mounts,
            "env": env,
        }
    finally:
        client.close()
