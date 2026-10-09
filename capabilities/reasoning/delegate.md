# Reasoning — Delegate

## ID
`reasoning.delegate`

## Purpose
Route a task and, if the local model is recommended, actually ask it. This closes the loop from
`reasoning.route`'s advisory recommendation to a real answer, so simple tasks get done locally
without spending Claude tokens.

## Unlike route, this one acts
`reasoning.route` never executes anything. This tool does: it calls `local_ai.ask` when the local
path is recommended. Don't extend the pattern (wiring it into `workflow.run` steps, adding
retry-then-escalate logic) without making the same deliberate, documented decision.

## Why this crossing is still safe
- **Only the local path runs automatically.** If `route` recommends "claude", this never fakes an
  answer and never calls Claude itself; it reports the decision so the caller (normally Claude)
  picks the task up directly.
- **A wrong "local" call is bounded and harmless.** Worst case: the local model gives a mediocre
  first-pass answer, returned with the full `route` breakdown so the caller can see why and redo the
  work. Nothing writes, nothing else triggers.
- **A failed local backend falls back to `"claude"`, not an error.**
- **No retry-then-escalate loop, no chaining into other tools.** One task in, one routing decision,
  one bounded action out.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `task` | string | — | Required. The task/request to route and possibly answer. |
| `token_estimate` | integer | `0` | Passed through to `reasoning.route` — see its docs for why real values matter. |
| `tool_count` | integer | `0` | Passed through to `reasoning.route`. |

## Returns
- `{"handled_by": "local", "answer": str, "model": str, "route": {...}}` — routed local, the
  local model answered.
- `{"handled_by": "claude", "route": {...}}` — routed to Claude, no answer attempted.
- `{"handled_by": "claude", "route": {...}, "note": str}` — routed local, but the local backend
  was unavailable; falls back to `"claude"` rather than erroring.

`route` is always the full `reasoning.route` result, so the caller sees *why* the decision was made.

## Example
```
delegate("what is the capital of France")
→ {"handled_by": "local", "answer": "The capital of France is Paris.",
   "model": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
   "route": {"recommendation": "local", "confidence": "high", "score": 3, ...}}

delegate("do a full security audit of our authentication flow")
→ {"handled_by": "claude",
   "route": {"recommendation": "claude", "confidence": "high", "score": 76, ...}}
```
Both recorded from real calls against a local Qwen2.5-Coder-7B.

## Errors
None beyond `reasoning.route`'s own argument-type errors. A local-backend failure is caught
internally and reported as `handled_by: "claude"`, never re-raised.

## Implementation
`tools/reasoning/delegate.py`. In-process calls to `reasoning.route` and `local_ai.ask`. Tests:
`tests/test_delegate.py`.

## Machine-readable definition
`delegate.json`, same directory — `"risk": "read"` (it only calls read-risk tools; no state changes).

## Related
- `reasoning.route` — the recommendation this acts on.
- `local_ai.ask` — what this calls on the local path.

## `background` (2026-10-07)
Optional standing instructions or background for the local model that must **not** decide the route: routing reads
only `task`, the local model gets `background` then `task`, and `background` counts toward `token_estimate` when that is 0.
Up to 20,000 characters. Use it for an agent's preamble or rules, so words like "password" or "vault" in a rule don't
send every task to Claude.
