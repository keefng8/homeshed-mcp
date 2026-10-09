# System — Health

## ID
`system.health`

## Purpose
Check whether the server's configured dependencies are reachable right now: memory-core, the first
local model, an app store backend and the Docker daemon. Deterministic, no LLM call: it hits each
service's own health endpoint and never runs inference or a real query. Useful for a session to check
its environment before starting work, not just for a dashboard.

## What it checks
| Check | Runs when | Calls |
|---|---|---|
| `memory_core` | `MEMORY_CORE_BASE_URL` is set | `<url>/health` |
| `local_ai_coder` | a local (not cloud) model is configured | the model server's own `/health` (a `/v1` suffix is stripped) |
| `mavis_strapi` | `MAVIS_STRAPI_BASE_URL` is set (an app store backend) | `<url>/_health` |
| `docker_daemon` | `DOCKER_HOST` is set, or Docker's default socket (`/var/run/docker.sock`) or Windows named pipe exists | the Docker SDK's `ping()` |

A service that isn't configured isn't checked, so a fresh install isn't reported unhealthy for services it
never asked for. Any 2xx answer counts as healthy (Strapi's `/_health` answers `204 No Content`).

## Parameters
None.

## Returns
```
{
  "ok": bool,
  "checks": {
    "memory_core": {"ok", "latency_ms", "error"},
    "local_ai_coder": {"ok", "latency_ms", "error", "model"},
    "mavis_strapi": {"ok", "latency_ms", "error"},
    "docker_daemon": {"ok", "latency_ms", "error"}
  }
}
```
Only the checks that ran are present. Top-level `ok` is true only if every check that ran passed.

## Errors
Never raises. Every check catches its own failure (unreachable host, non-2xx response, Docker daemon
down) into a normal `{ok: false, error: ...}` result.

## Not supported
Doesn't check cloud models, only the first local one. Doesn't check `web.read`'s external service
(Jina Reader): it's outside your control, so the answer wouldn't tell you anything you could act on.

## Implementation
`tools/system/health.py`. Uses `httpx` and the `docker` SDK's `client.ping()`.

## Machine-readable definition
`health.json`, same directory.

## Related
- `network.port_check` — the same idea (real reachability, not a guess), for any host.
- `system.info`
