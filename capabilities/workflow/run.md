# Workflow — Run

## ID
`workflow.run`

## Purpose
Chain existing tools into one multi-step task, for example deploy → verify → notify. It sequences
tools that already exist; it does no AI planning of its own.

## What this deliberately does not do
A step is only ever `{"capability": "<existing id>", "arguments": {...}}`, resolved against the real
registry. There is no "run this shell command" step type, and there must never be one. A workflow can
chain `dev.*` or `git.*` write tools, but only as ordinary steps with their own risk rules and their own
on/off switch, never as a new way to run commands. This tool adds sequencing, never new powers.

## Passing one step's output into a later step
Give a step an `"id"`, and any **later** step's `arguments` can reference its result:
`"$<id>"` for the whole result dict, or `"$<id>.key.subkey"` for a nested value. Works anywhere
in the arguments, including nested inside dicts/lists. This is value passing only: no DAG scheduler,
no parallel branches, no conditionals, no retries. Steps run strictly in list order.

Every `$id` reference is checked against **earlier** steps' ids before any step runs, so a typo or a
forward reference fails the whole call immediately. A `$id.path` whose path doesn't exist in that
step's *actual* result can only be caught at runtime: that step fails with the path in the error, and
execution stops. A string that starts with `$` but isn't a valid reference (e.g. `"$5.00"`) passes
through unchanged.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `steps` | list of objects | — | Required, non-empty. Each: `{"capability": "<id>", "arguments": {...}, "id": "<name>"}` — `arguments` optional (defaults to `{}`), `id` optional (only needed if a later step references this one). |

## Returns
`{"ok": bool, "completed": int, "total": int, "steps": [...]}`. Each entry in `steps` is
`{"capability", "ok": true, "result": <that tool's own return value>}` on success, or
`{"capability", "ok": false, "error": str}` on failure — and is always the *last* entry, since
execution stops at the first failure. `completed < total` means it stopped early; check the last
`steps` entry's `"error"` for why.

## Safety model
Every step is resolved against the real registry **before any step runs**: an unknown tool id in step
5 fails the whole call immediately, not after steps 1-4 already ran. Execution then stops at the first
failure.

Each step runs through the same wrapper as a direct call, so a tool that is switched off stays off
inside a workflow too. `workflow.run` never adds, removes or defaults a `dry_run` value: `arguments` is
passed straight through, so a write tool keeps *its own* default (`docker.container.restart`'s is
`dry_run=true`) unless the step explicitly sets `dry_run: false`.

## Errors
`ValueError` if `steps` is empty, a step isn't `{"capability": ..., ...}`, or a step references a
tool id that doesn't exist in the live registry — always raised before any step executes.
A step's own exception is caught and reported in its `steps` entry, not re-raised: the workflow
call itself only raises for a malformed *request*, never because a chained tool failed.

## Example
```json
{
  "steps": [
    {"capability": "docker.container.restart", "arguments": {"container": "my-app", "dry_run": false}},
    {"capability": "uptime.status", "arguments": {}},
    {"capability": "notify.send", "arguments": {"message": "my-app restarted", "priority": "default"}}
  ]
}
```

With output passing — clone a repo, then check its status wherever the clone landed:
```json
{
  "steps": [
    {"id": "c", "capability": "git.clone", "arguments": {"url": "https://example.com/r.git", "destination": "/tmp/r"}},
    {"capability": "git.status", "arguments": {"path": "$c.destination"}}
  ]
}
```

## Implementation
`tools/workflow/run.py`. Uses `registry.get_capability()` to resolve an id to the same logged wrapper
the MCP server itself calls, so a step run through a workflow shows up in `/activity` exactly like a
direct call. Tests: `tests/test_workflow_run.py`.

## Machine-readable definition
`run.json`, same directory — `"risk": "write"` (conservative: a workflow *can* chain write steps,
even though a given call might be all reads).

## Related
- `registry.find` — find which tool to use. `workflow.run` is what to do once you know the sequence.
