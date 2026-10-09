"""docker.network.list. See ../../capabilities/docker/network_list.md."""
from __future__ import annotations

import docker
from docker.errors import NotFound
from tools.docker._client import client as docker_client

from registry import tool


@tool(name="network.list", category="docker", doc="docker/network_list.md")
def list_networks() -> list[dict]:
    """List Docker networks on this host.

    Returns:
        One dict per network: id, name, driver, scope, containers (names attached to it, `null`
        if the network was removed between listing and reload — see note below, degrades that
        one field rather than failing the whole call, same defensive pattern as
        `docker.container.list`/`.inspect`, 2026-09-22).

    Note: the Docker Engine API's list-networks response omits attached containers (confirmed
    empirically — only its per-network inspect response includes them), so each network is
    reloaded individually to get an accurate `containers` list. One extra API call per network,
    not just per request — and therefore a real (if narrow) window for a network to be removed
    between the initial list and its own reload call.
    """
    client = docker_client()
    try:
        networks = client.networks.list()
        result = []
        for n in networks:
            try:
                n.reload()
                containers = [c.get("Name") for c in (n.attrs.get("Containers") or {}).values()]
            except NotFound:
                containers = None
            result.append(
                {
                    "id": n.short_id,
                    "name": n.name,
                    "driver": n.attrs.get("Driver"),
                    "scope": n.attrs.get("Scope"),
                    "containers": containers,
                }
            )
        return result
    finally:
        client.close()
