# Memory — Remember Fact

## ID
`memory.remember_fact`

## Purpose
Write a durable, high-value fact into the dedicated **facts tier** of persistent memory — a thin
wrapper around `memory.capture`, one `session_id` per **category**. See `capture.md`'s "Facts tier
vs. topic sessions" section for the convention this implements.

## When to use
Architecture decisions, current deployment layout, standing constraints — anything a future session
should see first, and that stays true until deliberately superseded. A good habit: record every real
bug found and fixed under `category="bugs-fixed"` as soon as the fix lands.

## When NOT to use
Implementation detail, bug write-ups, exact commands, anything that will be out of date within days.
Use `memory.capture` with a topic-specific `session_id` instead: this tool exists to keep that kind of
content *out* of the facts tier.

## Categories — separate logs, not one flat list
Each category is its own memory-core `session_id` (`facts` for `general`, `facts:<category>` for
everything else) — a separate log, not a tag on a shared list. That keeps `memory.recall_facts` fast
and free of unrelated noise: recalling one category never touches another's messages.

Suggested categories:

| Category | Contents |
|---|---|
| `general` | Default bucket; prefer a specific category. |
| `platform` | What the project is, and what it is not. |
| `known-gaps` | Standing, not-yet-resolved limitations. |
| `external-audits` | One-line USE/ADAPT/INSPIRE/IGNORE verdicts for researched external repos, pointing at the full write-up. |
| `bugs-fixed` | Every real bug found and fixed: root cause and fix, concise — a fast lookup list, not a narrative. A bug here is resolved; a `known-gaps` entry is still open. |
| `tasks` | In-progress/done markers. Write `TASK: <name> — IN PROGRESS: <what/why>` on start, restate as `TASK: <name> — DONE: <outcome>` on finish. Newest-first recall means a future session sees the current status at once, including anything an interrupted session left mid-flight. |
| `scripts` | Every reusable automation script, one entry each: path, purpose, when to run it. |

Pick an existing category when the fact fits one. Only add a new category when nothing fits — an
ever-growing category list defeats the purpose as much as one giant flat list would.

## Append-only — restate, don't describe the change
There is no update or delete for individual facts. When a fact changes, call this again with the
**full current statement** ("the app has 18 endpoints", not "added 2 endpoints").
`memory.recall_facts` returns newest first, so the current version sits above superseded ones in the
same category without any cleanup step.

Editing a stored fact isn't possible through memory-core's API: its query results come from its own
store, and changing files on disk doesn't change them. An edit tool that reported success without
really changing anything would be worse than none, so there isn't one.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `content` | string | — | Required. The fact to remember, max 16,000 chars (same limit as `memory.capture`). |
| `category` | string | `"general"` | Which facts log this belongs to. Lowercase letters/digits/hyphens, 1-40 chars. See the table above. |
| `project_dir` | string | `""` | Your working folder, e.g. `/home/you/my-app`. Memory is kept per project: a folder registered on the server (`GET`/`POST /projects`) uses that project's own memory (the longest match wins); any other folder gets its own memory on the built-in store, and the shared memory on memory-core; no folder uses the shared memory. Only the owner's sessions can choose: an app with its own client login always uses its own memory. An `X-Homelab-Project` header on the connection does the same. |
| `scope` | string | `"project"` | `"project"` saves to this project's (or this app's) own facts. `"global"` saves to the shared facts: the owner's write goes straight in; an app's waits until the owner approves it on the web panel (API access). |

## Returns
`{accepted: bool, message_id: str | None}`, the same shape as `memory.capture`, including its `pending` answer for
an app's `"global"` fact (the category is indexed once the owner approves it).

## Errors
Raises `MemoryError` — same failure modes as `memory.capture`, plus an invalid `category` (must
match `^[a-z0-9][a-z0-9-]{0,39}$`).

## Configuration
Same as `memory.capture` — see that doc's Configuration table.

## Implementation
`tools/memory/remember_fact.py` — calls `tools.memory.capture.capture()` with
`facts_session_id(category)`. Tests: `tests/test_memory_facts.py`.

## Machine-readable definition
`remember_fact.json`, same directory.

## Related
- `memory.recall_facts` — reads back what this writes.
- `memory.capture` — the general-purpose tool this wraps.
