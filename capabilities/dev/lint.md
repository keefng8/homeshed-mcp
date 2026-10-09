# Dev — Run Lint

## ID
`dev.lint`

## Purpose
Run an arbitrary lint/code-quality command in a given working directory. Same shape and risk
model as `dev.build`/`dev.test` — see `dev.python`'s doc for the full risk writeup, `dev.build`'s
doc for why this takes a full command rather than a fixed binary.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `command` | array | — | Required. Full argv (e.g. `["ruff", "check", "."]`, `["eslint", "."]`). |
| `cwd` | string | — | Required. Working directory. |
| `timeout` | integer | `120` | Seconds before the process is killed (max 600). |

## Returns
`{"exit_code": int, "stdout": str, "stderr": str}` — both streams truncated to their last 20,000
characters if longer.

## Errors
`DevError` — `cwd` doesn't exist, `command` empty, timeout out of range, the referenced binary
isn't on PATH (no linter is installed in this container by default — `ruff`/`eslint`/etc would
need adding), or timed out.

## Implementation
`mcp-server/tools/dev/lint.py`, shared runner in `tools/dev/_run.py`. Tests:
`mcp-server/tests/test_dev_lint.py` — run for real against a Python-based command this
container's actual toolchain can execute, plus a "binary not found" case.

## Machine-readable definition
`lint.json` — `"risk": "write"`.

## Related
- `dev.build`/`dev.test` — same general-command shape, different purpose framing.
