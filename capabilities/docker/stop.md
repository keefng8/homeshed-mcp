# Docker — Stop Container

## ID
`docker.container.stop`

## Purpose
Stop a running container. Same write-risk pattern as `docker.container.restart` — see that doc's
"Confirmation model" section, identical here.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `container` | string | — | Required. Container name or ID. |
| `dry_run` | bool | `true` | Report only when true (default); actually stop when false. |
| `timeout` | integer | `10` | Seconds to wait for graceful stop before killing, 0-300. Ignored when `dry_run=true`. |

## Returns
`{container, status_before, dry_run, stopped, status_after}` — `status_after` is `null` unless a
real stop happened.

## Errors
`docker.errors.NotFound` if the container doesn't exist. `ValueError` if `timeout` is out of range.

## Implementation
`mcp-server/tools/docker/container_stop.py`.

## Machine-readable definition
`stop.json`, same directory — `"risk": "write"`.

## Related
- `docker.container.list`
- `docker.container.start`
- `docker.container.restart`
