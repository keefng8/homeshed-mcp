# Docker — Container Logs

## ID
`docker.container.logs`

## Purpose
Fetch recent log lines from a container — the third of the read-only Docker trio
(`docker.container.list`, `docker.container.inspect`, `docker.container.logs`).

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `container` | string | — | Container name or ID. Required. |
| `tail` | integer | `200` | Number of most recent lines, 1-2000. |

## Returns
`{container, lines: [str]}` — each line timestamped by Docker.

## Errors
Raises `ValueError` if `tail` is out of range. `docker.errors.NotFound` (no container matches)
propagates uncaught, same as `docker.container.restart`/`.stop`/`.start` — the SDK's own message
already names the container.

## Implementation
`mcp-server/tools/docker/container_logs.py`. Same `DOCKER_HOST` targeting as the other Docker
capabilities.

## Machine-readable definition
`logs.json`, same directory.

## Related
- `docker.container.list`
- `docker.container.inspect`
