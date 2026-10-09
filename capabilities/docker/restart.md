# Docker — Restart Container

## ID
`docker.container.restart`

## Purpose
Restart a running (or stopped) container. **The first write-risk capability in this registry** —
every other built capability so far is read-only. Read this doc before calling it.

## Confirmation model
There's no interactive confirmation channel available to an MCP tool call — the caller either
passes the parameters for the real action or it doesn't happen. So the confirmation step is
built into the parameter itself:

- `dry_run=true` (**the default**): reports the container's current status and that a restart
  would happen — touches nothing. Always safe to call.
- `dry_run=false`: actually restarts the container. Only pass this once you've seen the dry-run
  result and mean it.

Never call with `dry_run=false` on a hunch. Call once with the default first, look at
`status_before`, decide, then call again with `dry_run=false` if the restart is actually wanted.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `container` | string | — | Required. Container name or ID. |
| `dry_run` | bool | `true` | See above. |
| `timeout` | integer | `10` | Seconds to wait for graceful stop before killing, 0-300. Ignored when `dry_run=true`. |

## Returns
`{container, status_before, dry_run, restarted, status_after}` — `status_after` is `null` unless
a real restart happened.

## Errors
`docker.errors.NotFound` if the container doesn't exist — not caught/wrapped, the SDK's own
message already names it. `ValueError` if `timeout` is out of range.

## Implementation
`mcp-server/tools/docker/container_restart.py`. Same `DOCKER_HOST` targeting as the other Docker
capabilities.

## Machine-readable definition
`restart.json`, same directory — `"risk": "write"`, the first one marked that way.

## Related
- `docker.container.list`
- `docker.container.inspect`
