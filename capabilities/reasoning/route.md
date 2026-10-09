# Reasoning — Route

## ID
`reasoning.route`

## Purpose
Recommend whether a task should go to the local model or Claude: the "can this be solved locally, or
does it need more?" step of `USER/TASK → LOCAL REASONING → local or Claude`. Combines
`local_ai.classify_complexity`'s score with its explicit `force_local`/`force_cloud` signals into one
recommendation.

## Why this is advisory, not automatic — read before wiring this into anything
This tool **never executes anything and never redirects a real request anywhere**. It answers a
question ("what would you recommend for this text?") when asked; it doesn't sit in front of anything
and intercept.

That matters because a plausible call can produce a wrong, confident answer. Asked about
`"design a distributed microservice architecture with event sourcing and handle race conditions
across 5000 lines of code"` with `token_estimate=0` and `tool_count=0`, it returned
`{"recommendation": "local", "confidence": "high", "score": 10}`: wrong. The request didn't match
`classify_complexity`'s patterns closely enough, and with no context size supplied, the up to 40
points that context contributes were missing. A person or Claude reading that answer can see it's
wrong and override it; a silent automatic router couldn't. **Always pass real
`token_estimate`/`tool_count` when available, and treat a call with both at 0 as less certain than
its `confidence` field suggests.**

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `text` | string | — | Required. The request text to route. |
| `token_estimate` | integer | `0` | Rough token count of the full context — pass the real value when known, see above. |
| `tool_count` | integer | `0` | Number of tools available/likely invoked, if known. |

## Returns
`{"recommendation": "local"|"claude", "confidence": "high"|"medium"|"low", "score": int,
"reasoning": str}`. `confidence` reflects distance from the decision threshold, not certainty about the
task itself. `reasoning` names which `classify_complexity` sub-scores contributed, so a wrong call can
be diagnosed, not just distrusted.

## Optional second opinion (Shedkeeper, experimental)
Shedkeeper is a small local decision model: a JEV-like system clone, inspired by Laya. Off by default: measured on 166 calls, it changed no result, so it only added latency. Set
`ROUTE_ASK_SHEDKEEPER=1` to switch it back on. With it on and a Shedkeeper classifier configured (`SHEDKEEPER_URL`,
a companion service not included in this package), route asks it only in borderline cases: when its own rules say "local" and either a judgement
word matched (security, deploy, credentials and similar) or the score is 25 or more (`SHEDKEEPER_FROM`). Shedkeeper
can only upgrade "local" to "claude", never the other way, and only at p 0.75 or more
(`SHEDKEEPER_UPGRADE`). Without it, route uses its rules alone. Every call is logged as numbers only, never
the request text, to `ROUTE_LOG_FILE` (default `DATA_DIR/usage/route_calibration.jsonl`) for
calibration.

## Errors
None beyond `classify_complexity`'s own argument-type errors.

## Implementation
`tools/reasoning/route.py`. In-process call to `local_ai.classify_complexity`. The threshold
(`_LOCAL_THRESHOLD = 35`) is a starting point, not tuned against real usage. Tests:
`tests/test_route.py`.

## Machine-readable definition
`route.json`, same directory — `"risk": "read"` (pure computation).

## Related
- `local_ai.classify_complexity` — what this wraps.
- `reasoning.delegate` — acts on this recommendation.
- `local_ai.ask` — the local-model call this informs.
