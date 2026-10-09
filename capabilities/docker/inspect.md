# Docker — Inspect Container

## ID
`docker.container.inspect`

## Purpose
Full config, networking and mount detail for one container — the filtered/summarized view, not
the raw Docker API dump (Recommendations doc §23: tool results should be compressed before
reaching an LLM, not passed through raw).

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `container` | string | — | Container name or ID. Required. |
| `include_env_values` | bool | `false` | Include env var *values*, not just names. |

## Security note
Env vars commonly carry secrets (DB passwords, API keys). Default response gives var **names**
only. `include_env_values=true` is a materially higher-risk call than the base read — if a
policy/confirmation layer gets built (Recommendations doc §11), gate this flag specifically, not
just the capability as a whole.

## Returns
`{id, name, image, status, networks, ports, mounts, env}` — `networks` is `{name: {ip, gateway}}`,
`mounts` is `[{source, destination, mode}]`. `image` is `"<unknown>"` if the container's own
image was since removed — same real failure mode as `docker.container.list`, found live
2026-09-22, fixed the same way in both.

## Errors
`docker.errors.NotFound` (no container matches) propagates uncaught — the SDK's own message
already names the container, same pattern as `docker.container.restart`/`.stop`/`.start`.

## Implementation
`mcp-server/tools/docker/container_inspect.py`. Same `DOCKER_HOST` targeting as
`docker.container.list` — see `list.md`.

## Machine-readable definition
`inspect.json`, same directory.

## Related
- `docker.container.list`
- `docker.container.logs`
- `docker.container.restart`
