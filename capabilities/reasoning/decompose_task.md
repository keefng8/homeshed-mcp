# Reasoning — Decompose Task

## ID
`reasoning.decompose_task`

## Purpose
Break a task description into an ordered list of subtasks — the second piece of the "local
reasoning" layer, after `reasoning.solve`. Unlike `solve` (pure Z3 computation, no LLM
involved), decomposition genuinely needs language understanding, so this delegates to
`local_ai.ask` rather than faking it with a regex heuristic the way `local_ai.classify_complexity`
does — per `local-ai-operating-rules.md`'s own standing rule: local model first for exactly this
kind of tedious, well-bounded task.

## Where this came from
Researched `reference-repos/cognitive-workspace` (MIT, tao-hpu) for its Metacognitive
Controller's task-decomposition approach — turned out to be a single unstructured prompt
("Decompose this task into subtasks... Output format: List of subtasks") with free-text response
parsing, nothing sophisticated enough to port directly. This capability improves on that pattern:
a stricter prompt asking for a JSON array specifically, defensive parsing (extracts the first
`[...]` substring rather than assuming the whole response is clean JSON), and one automatic retry
with a stricter instruction if the first response doesn't parse — the local 7B model gets the
format right often enough that failing outright on the first miss would be needlessly strict.
Verified live against the real deployed model (Qwen2.5-Coder-7B): a realistic task ("deploy a new
version of a web service and verify it is healthy") produced 8 sensible, correctly-ordered
subtasks on the first attempt, no retry needed.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `task` | string | — | Required. The task to decompose, in plain language. |
| `max_subtasks` | integer | `8` | Upper bound on subtask count, clamped to 1-20. |

## Returns
`{"task": str, "subtasks": [str, ...], "model": str}`. `subtasks` preserves the model's own
ordering — treat that as a *suggested* sequence, not a verified-correct one. `model` names which
local model actually answered (useful if this ever gets called against more than one).

## What this is for right now
Purely advisory, same as every other `reasoning.*`/`local_ai.*` capability. Nothing calls this
automatically, and it doesn't build or execute anything itself — the natural next step for a
caller is mapping each returned subtask onto a real capability and feeding the result into
`workflow.run`'s own `steps`, but that mapping is the caller's job, not this capability's.

## Errors
`ValueError` if `task` is empty/whitespace-only. `RuntimeError` if the local AI backend is
unavailable (`local_ai.ask`'s own `LocalAIError`, re-raised with context), or if its response
still doesn't parse as a JSON array of strings after one retry.

## Stress-tested, 2026-09-24
9 live scenarios against the real deployed model: simple, vague ("fix the bug"), trivial/non-
technical, apostrophe/quote-containing, a real refactor task, empty input (correctly raises),
non-English (Chinese in, Chinese out, correctly decomposed and ordered — not a requirement, but a
good robustness sign), `max_subtasks=1` boundary (correctly truncates to exactly 1), and a long
5-clause task (correctly captured every clause across 8 ordered subtasks). Zero bugs found, and
the JSON-array-only prompt got the format right on the first attempt every single time — the
retry path exists but was never actually needed across this battery.

## Implementation
`mcp-server/tools/reasoning/decompose_task.py`. Calls `tools.local_ai.ask.ask` directly (in-
process, not a second MCP round-trip) with `temperature=0.0` for determinism. Tests:
`mcp-server/tests/test_decompose_task.py` — mocks `ask` (no live GPU/network needed for the test
suite; the live verification above was a one-time manual check, not part of the automated tests).

## Machine-readable definition
`decompose_task.json`, same directory — `"risk": "read"` (advisory computation; the only I/O is
the existing, already-classified `local_ai.ask` call it delegates to).

## Related
- `local_ai.ask` — what this calls under the hood.
- `reasoning.solve` — the other half of the "local reasoning" layer; pure computation vs. this
  capability's language-understanding-dependent approach.
- `workflow.run` — the natural next step once subtasks are mapped to real capability calls.
