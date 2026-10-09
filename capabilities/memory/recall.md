# Memory — Recall

## ID
`memory.recall`

## Purpose
Read back raw conversation history previously written with `memory.capture` for a given
`session_id`. Exactly what was written, in order — not semantic search or L1/L2/L3 distillation
(memory-core's `/v3/conversation/query`, not a recall/search endpoint). See `capture.md`
for the full context.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `session_id` | string | — | Required. The `session_id` used when capturing. |
| `limit` | integer | `20` | Max messages to return, 1-100. |
| `max_chars` | integer | `0` | Cut each returned message to this many characters (ending "…", marked `"truncated": true`); 0 returns the full text. Keeps a routine memory check cheap. |
| `scope` | string | `"project"` | `"project"` reads this project's own memory; `"global"` reads the shared memory (facts every project may need): the owner always, and an app unless the owner switched its shared reads off on the web panel (API access). |
| `project_dir` | string | `""` | Your working folder, e.g. `/home/you/my-app`. Memory is kept per project: a folder registered on the server (`GET`/`POST /projects`) uses that project's own memory (the longest match wins); any other folder gets its own memory on the built-in store, and the shared memory on memory-core; no folder uses the shared memory. Only the owner's sessions can choose: an app with its own client login always uses its own memory. An `X-Homelab-Project` header on the connection does the same. |

## Returns
`{messages: [{id, role, content, timestamp}], total: int}`

## Errors
Raises `MemoryError` — not configured, backend unreachable/timed out, or memory-core rejected the
query.

## Configuration
Nothing by default (the built-in store), or memory-core's variables once `MEMORY_CORE_BASE_URL` is set: the same
as `memory.capture` — see that doc's Configuration section. Both tools share
`tools/memory/capture.py`'s `_config()`.

## Implementation
`tools/memory/recall.py`. Tests: `tests/test_memory.py` (mocked).

## Machine-readable definition
`recall.json`, same directory.

## Related
- `memory.capture` — writes what this reads.
