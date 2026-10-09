# Local AI — Classify Complexity

## ID
`local_ai.classify_complexity`

## Purpose
Score a request's complexity 0-100 with a fast, deterministic, offline heuristic. Ported from
[Lynkr](https://github.com/Fast-Editor/Lynkr)'s Rust module (`native/src/lib.rs`,
`analyze_complexity_native`; Apache-2.0, see `THIRD_PARTY.md`): regex-pattern scoring across token
count, tool count, task-type keywords, code-complexity keywords and reasoning keywords, plus explicit
force-local/force-cloud override patterns.

It started as a faithful port and is **no longer strictly faithful**: `FORCE_CLOUD_PATTERNS` has three
broader patterns, because the original literal-phrase patterns missed two dangerous, natural phrasings
("please review our auth system for vulnerabilities" and "help me debug this race condition deadlock in
production" both scored as `local`). The added patterns are keyword-based
(`review/audit/check/assess` near `vulnerab`; bare `deadlock`/`race condition` mentions).

**Not ported:** Lynkr's separate KNN router, which needs an embedding model and a warmed-up index of
real query outcomes. This tool only **scores**; it never routes or runs anything. Lynkr's README cites
a benchmark for its *full* hybrid system; this is the heuristic layer alone, so treat its accuracy as
unmeasured.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `text` | string | — | Required. The request text to score. |
| `token_estimate` | integer | `0` | Rough token count of the full context, if known. |
| `tool_count` | integer | `0` | Number of tools available/likely invoked, if known. |

## Returns
`{"score": 0-100, "force_local": bool, "force_cloud": bool, "token_score", "tool_score",
"task_type_score", "code_complexity_score", "reasoning_score"}`. `force_local` short-circuits
everything to `score=0` (a bare greeting/ack/"what time is it"). `force_cloud` floors the score
at 76 (patterns like "security audit", "PR review" that should never read as trivial regardless
of length). The per-component scores show *why* a score landed where it did.

**Quirk worth knowing before trusting `task_type_score` alone:** `SIMPLE_QUESTION_RE` matches any
string starting with `do`/`does`/`can`/etc, so an imperative request like "do a full security audit
of our auth flow" matches as if it were a simple question (`task_type_score=3`). This is inherited from
Lynkr's own pattern. The overall `score` still comes out right in that example (76,
`force_cloud=true`), but check `score` and the force flags first.

## What this is for
Purely advisory. Nothing calls it automatically, and it decides nothing by itself. It gives a person,
or Claude deciding whether to delegate to `local_ai.ask`, a fast, free, explainable second opinion —
the same role `registry.find` plays for "which tool", for "how hard is this".

## Errors
None beyond normal argument-type errors. Empty/whitespace text scores like any other
non-matching short string — not treated as invalid input.

## Implementation
`tools/local_ai/classify_complexity.py`. Pure Python, standard library `re` only. Tests:
`tests/test_classify_complexity.py`, including exact boundary-value checks against the original Rust
score bands (an easy place to introduce an off-by-one when porting).

## Machine-readable definition
`classify_complexity.json`, same directory — `"risk": "read"` (pure computation, no I/O).

## Related
- `reasoning.route` — turns this score into a local-or-Claude recommendation.
- `registry.find` — the same "advisory, not automatic" pattern.
