# Docker — Start Container

## ID
`docker.container.start`

## Purpose
Start a stopped container. Same write-risk pattern as `docker.container.restart`/`.stop` — see
`restart.md`'s "Confirmation model" section, identical here. Lower blast radius than
restart/stop (nothing running gets interrupted), but still gated the same way since starting the
wrong container is still an unwanted side effect.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `container` | string | — | Required. Container name or ID. |
| `dry_run` | bool | `true` | Report only when true (default); actually start when false. |

No `timeout` parameter — the Docker SDK's `start()` doesn't take one.

## Returns
`{container, status_before, dry_run, started, status_after}` — `status_after` is `null` unless a
real start happened.

## Errors
`docker.errors.NotFound` if the container doesn't exist.

## Implementation
`mcp-server/tools/docker/container_start.py`.

## Machine-readable definition
`start.json`, same directory — `"risk": "write"`.

## Related
- `docker.container.list`
- `docker.container.stop`
- `docker.container.restart`
