# Docker — List Containers

## ID
`docker.container.list`

## Purpose
List containers on the Docker host this server runs on (or is pointed at via `DOCKER_HOST`).

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `all` | bool | `false` | Include stopped containers, not just running ones. |

## Returns
List of `{id, name, image, status, ports}` per container. `image` is `"<unknown>"` for a
container whose image was since removed (real failure mode, found live 2026-09-22 — a container
rebuilt many times in one session can leave its own prior image dangling and pruned) — degrades
that one field rather than failing the whole call.

## Errors
No input to validate (`all` is a plain boolean). Only fails on a genuine SDK/daemon-level
problem (Docker unreachable, permission denied on the socket) — those propagate uncaught.

## Implementation
`mcp-server/tools/docker/list_containers.py` — uses the `docker` Python SDK (`docker.from_env()`),
so it talks to whatever `DOCKER_HOST`/socket the server process sees. On the deploy target that's
the local Docker socket; against the other host, set `DOCKER_HOST=tcp://<host>:2375` (or SSH:
`ssh://user@host`) before starting the server, or run one server instance per host.

## Machine-readable definition
`list.json`, same directory — see `Mavis_Capability_Intelligence_Engine_Recommendations.md`
§2-4 for why this exists alongside the prose: registry loading/validation/permissions should read
the JSON, not parse this file.

## Related
- `docker.container.inspect`
- `docker.container.logs`
- `docker.container.restart`
