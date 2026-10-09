# Dev — Run Python

## ID
`dev.python`

## Purpose
Run `python <args>` in a given working directory. **Real code execution, in the server's own process,
no sandbox, no confirmation step.** This is the most powerful tool in the server: anyone who can call it
can run any Python the server's user can run. In Docker, if you mounted the Docker socket, that includes
controlling Docker.

It is off by default, and `ENABLE_TOOLS=*` does **not** switch it on: name it explicitly
(`ENABLE_TOOLS=dev.python`). Only do that on a server that only you can reach, and keep its token safe.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `args` | array | — | Required. Arguments passed to `python` (e.g. `["script.py"]`, `["-m", "pytest"]`). |
| `cwd` | string | — | Required. Working directory. |
| `timeout` | integer | `120` | Seconds before the process is killed (max 600). |

`args` is an argv list, never a shell string, so there is no `&&`, pipe or other shell expansion.

## Returns
`{"exit_code": int, "stdout": str, "stderr": str}` — both streams truncated to their last 20,000
characters if longer.

## Errors
`DevError` — `cwd` doesn't exist, `args` empty, timeout out of range, `python` not on PATH, or timed out.

## Implementation
`tools/dev/python.py`, shared runner in `tools/dev/_run.py`. Tests: `tests/test_dev_python.py`, run
with a real `python` subprocess, not mocked.

## Machine-readable definition
`python.json` — `"risk": "write"`.

## Related
- `dev.test` / `dev.build` / `dev.lint` — same risk, general-command variants.
