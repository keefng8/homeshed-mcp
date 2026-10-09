# Docker — List Networks

## ID
`docker.network.list`

## Purpose
List Docker networks on this host, including which containers are attached to each — same
read-only pattern as `docker.container.list`.

## Parameters
None.

## Returns
List of `{id, name, driver, scope, containers}` — `containers` is the list of container names
currently attached to that network, or `null` if the network was removed between the initial
list and its own reload call (a real, if narrow, race — see below).

## Errors
No input to validate. A network removed mid-listing degrades that one entry's `containers` to
`null` rather than failing the whole call (2026-09-23, applied proactively after finding the
identical class of bug for real in `docker.container.list`/`.inspect`'s image lookups). Only a
genuine SDK/daemon-level problem propagates uncaught.

## Implementation detail worth knowing
The Docker Engine API's list-networks response does **not** include attached containers (confirmed
empirically against a real daemon, 2026-09-21 — only the per-network inspect response does), so
this capability reloads each network individually to get an accurate `containers` list. One extra
Docker API call per network returned, not just per request — fine at homelab scale, would need
reconsidering if network count ever got large.

## Implementation
`mcp-server/tools/docker/list_networks.py`. Tests: `mcp-server/tests/test_docker_network_list.py`
(mocked Docker SDK).

## Machine-readable definition
`network_list.json`, same directory.

## Related
- `docker.container.list`
- `docker.container.inspect`
