# Registry — Find

## Investigated 2026-09-24: replacing the local_ai_fallback tier with local_ai.decide

Considered, tested empirically, **not implemented — a real "doesn't help" finding, not a
skipped attempt.** `local_ai.decide` was built the same day and directly answers the gap this
capability's docs (`ARCHITECTURE.md`'s Phase 8 note) named — "confidence-scored instead of a flat
guess" — so replacing `ask()`'s free-text pick with a typed `decide()` call looked like an
obvious win. Real testing found otherwise:

- All 46 manifests at once: 18s, confidence ~0.01, essentially random (wrong answer).
- Narrowed to the top candidates by a naive keyword-overlap heuristic: the heuristic itself is
  unreliable — most manifests tied at the same low overlap score against a realistic query
  (stopwords dominate short descriptions), so "top 15" would be an arbitrary cut, not a real
  narrowing.
- **Hierarchical two-stage `decide()` (category, then capability within category)**: stage 1
  (16 categories, clearly distinct from each other) picked correctly, fast (0.34s). Stage 2
  (8 `docker` capabilities, closely related to each other) picked **wrong**
  (`docker.volume.list` for "why cant i reach my container") with near-uniform, low confidence
  across all 8 — even at a small N well within the range that worked for other tests.

Real conclusion: `local_ai.decide`'s zero-shot NLI classifier is good at distinguishing
*categorically different* options (its dashboard demo — billing/technical/sales — and this
capability's own "which category" stage both worked) but genuinely struggles with fine-grained
disambiguation among *semantically similar* options, independent of candidate count. That's
exactly this tier's actual job — picking one specific capability among several related ones in
the same domain. The existing free-text `ask()` approach already handles the tested query
correctly and isn't worse here; replacing it would trade a working mechanism for a worse one just
to use the newer tool. Not done. Revisit only if a real accuracy problem with the current `ask()`
approach shows up — this finding is about the wrong-tool-for-this-job question, not "not
tried."

## ID
`registry.find`

## Purpose
The Phase 8 fast capability router (`nextsteps.md`) — answers "which capability handles this
request?" without executing anything. Three tiers, cheapest and most deterministic first,
deliberately no vector DB or embeddings (`nextsteps.md` is explicit: "Do NOT immediately
introduce vector databases, complex embeddings pipelines" for the first router):

1. **Exact ID** — the query is literally a capability ID (`docker.container.list`). Instant,
   100% deterministic.
2. **Alias/keyword** — substring match against each capability's `aliases`, `id`, and `name`.
   Still deterministic, still fast.
3. **`local_ai.ask` fallback** — only when the first two find nothing. Hands the query and every
   capability's `id`+`description` to the local model, asks it to pick one. This is the
   "semantic search" tier `nextsteps.md`'s router diagram calls for, implemented with the local
   model already built rather than a new embeddings/vector-DB dependency — matches the project's
   own "prefer existing proven solution before building custom infrastructure" principle
   (`CLAUDE.md` → Research Before Reinventing) about as directly as it gets: the infrastructure
   already existed one capability over.

## When NOT to use
- When you already know the exact capability ID — just call it, don't route through this first.
- This never executes the capability it finds — always a separate call after.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `query` | string | — | Required. Natural-language request, or a capability ID directly. |
| `top_n` | integer | `3` | Max candidates from the alias/keyword tier. |

## Returns
`{tier, matches: [{id, name, description}]}`. `tier` tells you how much to trust the result —
`exact_id`/`alias_keyword` are deterministic; `local_ai_fallback` is a local model's guess, one
candidate only, worth a second look before acting on it; `no_match` means genuinely nothing fit.

## Errors
Raises `ValueError` if `query` is empty. `local_ai_fallback` tier failures (the local model
unreachable/misconfigured) are caught internally and degrade to `{"tier": "no_match", "matches":
[]}` rather than propagating `LocalAIError` — a routing capability shouldn't fail just because
its last-resort tier is unavailable.

## Known limitation, found while building this
`local_ai.ask`'s tier-3 fallback inherits its behavior — Qwen3's reasoning mode occasionally
consumes an entire `max_tokens` budget without producing visible output for open-ended or
instruction-heavy prompts (confirmed while drafting this capability's own aliases: two attempts
at generation-style prompts returned empty at 150 and 600 tokens; a terser prompt at the full
1024 default worked). `find`'s prompt is short and constrained ("reply with only the id"), which
is the lower-risk prompt shape, but a `local_ai_fallback` result of `no_match` doesn't distinguish
"genuinely nothing fit" from "the model burned its budget thinking." Not fixed — `nextsteps.md`
explicitly says don't over-engineer the first router; worth revisiting if `no_match` rates turn
out high in practice.

## Implementation
`mcp-server/tools/registry/find.py`. Imports `tools.local_ai.ask` directly (capability-calling-
capability, not registry-calling-execution — `find` is itself a tool, same as any other).

Note the two different "registry" names in this codebase: `mcp-server/registry.py` (the
`@tool`-discovery module every capability uses) and `mcp-server/tools/registry/` (this
capability's own category folder, named to match `nextsteps.md`'s "Capability Registry"
terminology). Same word, different things — no code relationship between them.

## Machine-readable definition
`find.json`, same directory.

## Related
All capabilities, indirectly — this is what finds them.
