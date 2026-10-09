# Dev — Run Node.js

## ID
`dev.node`

## Purpose
Run `node <args>` in a given working directory. Same design and risk as `dev.python`: real code
execution in the server's own process, no sandbox. Read that doc before switching it on.

**Needs Node.js on the machine running the server.** The Docker image doesn't include Node.js, so in
Docker every call fails with `DevError` ("'node' is not installed or not on PATH"). Run with `uvx`, it
uses the `node` on your PATH.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `args` | array | — | Required. Arguments passed to `node` (e.g. `["script.js"]`). |
| `cwd` | string | — | Required. Working directory. |
| `timeout` | integer | `120` | Seconds before the process is killed (max 600). |

## Returns
`{"exit_code": int, "stdout": str, "stderr": str}` — both streams truncated to their last 20,000
characters if longer.

## Errors
`DevError` — `cwd` doesn't exist, `args` empty, timeout out of range, `node` not on PATH, or timed out.

## Implementation
`tools/dev/node.py`, shared runner in `tools/dev/_run.py`. Tests: `tests/test_dev_node.py` — the
"node not installed" path is tested as-is; the other assertions check that argv, cwd and timeout are
passed through, without needing a live `node`.

## Machine-readable definition
`node.json` — `"risk": "write"`.

## Related
- `dev.python` — same pattern.
- `dev.build` — general command runner, useful for `npm`-based builds.
