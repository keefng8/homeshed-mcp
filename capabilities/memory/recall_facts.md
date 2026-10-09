# Memory — Recall Facts

## ID
`memory.recall_facts`

## Purpose
Read back one **category** of the project's durable facts tier — a thin wrapper around
`memory.recall`, scoped to a single `session_id` per category. This is the standing knowledge base
a session should check first; see `capture.md`'s "Facts tier vs. topic sessions" and
`remember_fact.md`'s category table.

## Why scoped, not "recall everything"
There is deliberately no "give me every fact ever recorded" mode. Call this once per category
actually relevant to the current task — each call is small, fast, and free of unrelated content by
construction (categories are separate memory-core sessions, not a filter over one shared list).
Calling it for every known category back-to-back costs a handful of sub-second local HTTP calls;
that's still cheaper and clearer than one giant recall a reader has to skim.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `category` | string | `"general"` | Which facts log to read — see `remember_fact.md`'s category table for what's in use. |
| `limit` | integer | `20` | Max facts to return, 1-100. Lower than `memory.recall`'s general-purpose default — a category is meant to be small enough to read in full, not paginated. |
| `max_chars` | integer | `300` | Cut each returned message to this many characters (ending "…", marked `"truncated": true`); 0 returns the full text (the default until 2026-09-29). Keeps the per-request memory check cheap. |
| `scope` | string | `"project"` | `"project"` reads this project's own memory; `"global"` reads the shared memory (facts every project may need): the owner always, and an app unless the owner switched its shared reads off on the web panel (API access). |
| `project_dir` | string | `""` | Your working folder, e.g. `C:\Users\you\my-app` or `~/my-app`. Memory is kept per project: a folder registered on the tool server (`GET`/`POST /projects`) uses that project's own memory (the longest match wins); any other folder gets its own memory on the built-in store, and the shared memory on memory-core; no folder uses the shared memory. Only the owner's sessions can choose: an app with its own client login always uses its own memory. An `X-Homelab-Project` header on the connection does the same. |

## Returns
`{messages: [{id, role, content, timestamp}], total: int}` — same shape as `memory.recall`,
newest-first (so a superseded restatement of a fact sits below its current version).

## Errors
Raises `MemoryError` — same failure modes as `memory.recall`, plus an invalid `category`.

## Configuration
Same as `memory.capture`/`memory.recall` — see `capture.md`'s Configuration table.

## Known limitation
None currently — see `capture.md`'s "Known limitation" for the (now-resolved) reachability
history.

## Implementation
`mcp-server/tools/memory/recall_facts.py` — calls `tools.memory.recall.recall()` with
`facts_session_id(category)`. Tests: `mcp-server/tests/test_memory_facts.py`.

## Machine-readable definition
`recall_facts.json`, same directory.

## Related
- `memory.remember_fact` — writes what this reads.
- `memory.recall` — the general-purpose capability this wraps.
