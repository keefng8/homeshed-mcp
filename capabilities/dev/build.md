# Dev — Run Build

## ID
`dev.build`

## Purpose
Run an arbitrary build command in a given working directory. Same real-code-execution,
in-process, no-sandbox design as `dev.python`/`dev.node` — see `dev.python`'s doc for the full
risk writeup. No single universal "build" binary exists across project types (npm, make, cargo,
docker build, ...), so unlike `dev.python`/`.node` (which always invoke a fixed binary), this
capability takes the **full command** from the caller. That's strictly more general-purpose than
the other `dev.*` capabilities — worth knowing before reaching for it.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `command` | array | — | Required. Full argv (e.g. `["npm", "run", "build"]`, `["make"]`). |
| `cwd` | string | — | Required. Working directory. |
| `timeout` | integer | `300` | Seconds before the process is killed (max 900 — the longest default/ceiling of the `dev.*` set, since builds are typically the slowest). |

## Returns
`{"exit_code": int, "stdout": str, "stderr": str}` — both streams truncated to their last 20,000
characters if longer.

## Errors
`DevError` — `cwd` doesn't exist, `command` empty, timeout out of range, the referenced binary
isn't on PATH (this container has whatever `python:3.12-slim` plus this project's own installed
Python packages provide — no `npm`/`make`/etc unless separately added), or timed out.

## Implementation
`mcp-server/tools/dev/build.py`, shared runner in `tools/dev/_run.py`. Tests:
`mcp-server/tests/test_dev_build.py` — run for real against commands this container's actual
toolchain can execute (Python-based), plus a "binary not found" case for an unavailable one.

## Machine-readable definition
`build.json` — `"risk": "write"`.

## Related
- `dev.test`/`dev.lint` — same general-command shape, different purpose framing.
