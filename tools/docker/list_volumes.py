"""docker.volume.list. See ../../capabilities/docker/volume_list.md."""
from __future__ import annotations

import docker
from tools.docker._client import client as docker_client

from registry import tool


@tool(name="volume.list", category="docker", doc="docker/volume_list.md")
def list_volumes() -> list[dict]:
    """List Docker volumes on this host.

    Returns:
        One dict per volume: name, driver, mountpoint, created_at.
    """
    client = docker_client()
    try:
        volumes = client.volumes.list()
        return [
            {
                "name": v.name,
                "driver": v.attrs.get("Driver"),
                "mountpoint": v.attrs.get("Mountpoint"),
                "created_at": v.attrs.get("CreatedAt"),
            }
            for v in volumes
        ]
    finally:
        client.close()
