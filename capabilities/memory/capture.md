# Memory — Capture

## ID
`memory.capture`

## Purpose
Write one message into persistent, cross-session memory: the built-in store (SQLite, nothing to set up),
or a self-hosted [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory)
`memory-core` once it's configured. The server calls memory-core's own API directly, not through its chat proxy — see
"Why not the proxy" below.

## When to use
A fact, decision, or preference worth recalling in a *future session*, not this one — `memory.recall`
reads it back later. Anything that only matters to the current conversation doesn't belong here.

## Facts tier vs. topic sessions
The store holds two kinds of content, and they should not share a `session_id`.

- **Facts tier** (`memory.remember_fact`/`memory.recall_facts`) — durable, high-value truths about
  the project's current state: architecture decisions, deployment layout, standing constraints.
  This is what a future session should read first. It is split into **categories**: each category
  is its own `session_id` (`facts` for the default `general` category, `facts:<category>` for every
  other one), so a caller recalls one small, relevant slice instead of the whole facts tier. See
  `remember_fact.md` for the category list and validation rule, `recall_facts.md` for scoped recall.
- **Topic sessions** (`memory.capture`/`memory.recall`, caller-chosen `session_id`) — implementation
  detail, bug write-ups, exact commands, anything that will be out of date within days. Use a stable
  topic slug (e.g. `my-app-build-log`) so it stays queryable but never competes with the facts tier.

Rule of thumb: if a future session reading only the facts tier would miss something it needs to know
about the project's current state, it belongs in facts. If it's "how we got here" rather than "where we
are", it belongs in a topic session.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `session_id` | string | — | Required. Groups related messages — `memory.recall` reads back everything under the same `session_id`. Pick something stable (a project slug, a topic name). |
| `content` | string | — | Required. The text to remember, max 16,000 chars. |
| `role` | string | `"user"` | `"user"` (a fact being told to the system) or `"assistant"` (something generated). |
| `project_dir` | string | `""` | Your working folder, e.g. `/home/you/my-app`. Memory is kept per project: a folder registered on the server (`GET`/`POST /projects`) uses that project's own memory (the longest match wins); any other folder gets its own memory on the built-in store, and the shared memory on memory-core; no folder uses the shared memory. Only the owner's sessions can choose: an app with its own client login always uses its own memory. An `X-Homelab-Project` header on the connection does the same. |
| `scope` | string | `"project"` | `"project"` writes this project's (or this app's) own memory. `"global"` writes the shared memory: the owner's write goes straight in; an app's waits until the owner approves it on the web panel (API access). |

## Returns
`{accepted: bool, message_id: str | None}`. An app's `"global"` write isn't stored yet: `{accepted: false, message_id:
null, pending: "<id>", note}`. It goes in once the owner approves it, or is dropped if the owner declines.

## Errors
Raises `MemoryError` — not configured, backend unreachable/timed out, or memory-core rejected the
write (bad identity, malformed request).

## Configuration
**Nothing, by default.** Without `MEMORY_CORE_BASE_URL`, every memory tool uses the built-in store: one SQLite
file at `MEMORY_DB_PATH`, else `DATA_DIR/usage/memory.db`. Each project gets its own memory automatically. An app
with its own client login always has its own memory (`app:<name>`, or an agent the owner set), never the shared
one. The owner's sessions use the project registered for the folder, else the folder itself (`project_dir`, the
`X-Homelab-Project` header, or `CLAUDE_PROJECT_DIR`), else `shared` (which `scope="global"` also reads).
On memory-core, an app without an agent gets one made on its first memory call (kept on its client entry); if
memory-core won't make one, the call fails rather than use the shared memory.
`homeshed-mcp doctor` shows which store is in use and how much it holds.

Set `MEMORY_CORE_BASE_URL` to use memory-core instead; nothing is copied between the two stores. Then these are
all required — `capture()` fails fast naming what's missing rather than silently doing nothing:

| Var | Purpose |
|---|---|
| `MEMORY_CORE_BASE_URL` | memory-core's gateway, e.g. `http://memory-core:8420` when both run on the same Docker network, or `http://localhost:8420`. |
| `MEMORY_CORE_BEARER` | memory-core's gateway bearer key. Use a generated key, not a placeholder. |
| `MEMORY_SERVICE_ID` | Sent as `x-tdai-service-id` (memory-core's default is `"default"`). |
| `MEMORY_USER_KEY` | Sent as `x-tdai-user-key`: the key of the user memory-core bootstrapped. |
| `MEMORY_TEAM_ID`, `MEMORY_USER_ID`, `MEMORY_AGENT_ID` | That same user's IDs. memory-core's own identity logic has no fallback for these, so this tool needs all three or none. |

## Why not the proxy
memory-core's proxy is what a coding-agent client points its own LLM connection at. Going through its
`/v1/chat/completions` means paying for session setup, a conversation id, and team/agent headers: real
overhead for what's really "write one fact". Calling memory-core's own `/v3/conversation/add` directly,
as the same user, is simpler and never touches the LLM path.

## Network
The server must be able to reach `MEMORY_CORE_BASE_URL`. If both run in Docker, put them on the same
Docker network and use memory-core's service name; memory-core can then stay bound to loopback on the
host, with no LAN exposure.

## Implementation
`tools/memory/capture.py`. Tests: `tests/test_memory.py` (mocked, no live memory-core needed).

## Machine-readable definition
`capture.json`, same directory.

## Related
- `memory.recall` — reads back what this writes.
