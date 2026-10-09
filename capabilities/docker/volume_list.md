# Docker — List Volumes

## ID
`docker.volume.list`

## Purpose
List Docker volumes on this host — same read-only pattern as `docker.container.list`.

## Parameters
None.

## Returns
List of `{name, driver, mountpoint, created_at}`.

## Errors
No input to validate. Only fails on a genuine SDK/daemon-level problem — those propagate
uncaught. No per-item lookup that could fail independently (unlike `docker.network.list`'s
per-network reload) — `.attrs` is already populated from the initial list response.

## Implementation
`mcp-server/tools/docker/list_volumes.py`. Tests: `mcp-server/tests/test_docker_volume_list.py`
(mocked Docker SDK).

## Machine-readable definition
`volume_list.json`, same directory.

## Related
- `docker.container.list`
- `docker.container.inspect`
