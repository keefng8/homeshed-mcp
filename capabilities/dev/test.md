# Dev — Run Tests

## ID
`dev.test`

## Purpose
Run an arbitrary test-suite command in a given working directory. Same shape and risk model as
`dev.build` — see `dev.python`'s doc for the full risk writeup, `dev.build`'s doc for why this
takes a full command rather than a fixed binary.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `command` | array | — | Required. Full argv (e.g. `["pytest", "tests/", "-v"]`, `["npm", "test"]`). |
| `cwd` | string | — | Required. Working directory. |
| `timeout` | integer | `300` | Seconds before the process is killed (max 900). |

## Returns
`{"exit_code": int, "stdout": str, "stderr": str}` — both streams truncated to their last 20,000
characters if longer.

## Errors
`DevError` — `cwd` doesn't exist, `command` empty, timeout out of range, the referenced binary
isn't on PATH, or timed out.

## Implementation
`mcp-server/tools/dev/test.py`, shared runner in `tools/dev/_run.py`. Tests:
`mcp-server/tests/test_dev_test.py` — run for real; `pytest` itself is available in this
container only via the project's own `[test]` extras (`pip install -e ".[test]"`), not in the
production image by default — a real, pre-existing gap noted in `ARCHITECTURE.md` before this
capability existed, still true now: calling `dev.test` with a `pytest`-based command against the
deployed production container will fail with "not installed" unless that extra is added to the
base install.

## Machine-readable definition
`test.json` — `"risk": "write"`.

## Related
- `dev.build`/`dev.lint` — same general-command shape, different purpose framing.
