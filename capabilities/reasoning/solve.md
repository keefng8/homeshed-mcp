# Reasoning — Solve

## ID
`reasoning.solve`

## Purpose
Solve a constraint-satisfaction or logic problem with Z3 (Microsoft's SMT solver). Not an LLM call and
not a general reasoning engine: it is useful for problems that reduce to satisfiability, such as "is
this set of constraints consistent", "does this schedule or configuration have a valid assignment" or
"find values satisfying these conditions".

## Why SMT-LIB2, not a custom format
The input is SMT-LIB2, the standard text format Z3's own CLI and every other SMT solver accepts. It is
also safe to accept from a caller: SMT-LIB2 is purely declarative (it describes a logic problem and
cannot read a file, open a socket or run a command), unlike raw Python calls to Z3's API.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `smt_lib2` | string | — | Required. Standard SMT-LIB2 source, must include `(check-sat)`. |
| `timeout_ms` | integer | `5000` | Solver time budget. Clamped to 1-60000, not rejected if out of range. |

## Returns
`{"result": "sat"|"unsat"|"unknown", "model": {var: value, ...} | None, "timeout_ms": int}`.
`model` is only populated when `result == "sat"` — one satisfying assignment, not every possible
one. `"unknown"` means the timeout was hit before Z3 could decide, not that the input was
malformed (a malformed input raises `ValueError` instead, before `check()` is ever called).

## Example
```
smt_lib2 = "(declare-const x Int) (declare-const y Int) (assert (> x 0)) (assert (= (+ x y) 10)) (check-sat)"
→ {"result": "sat", "model": {"y": "9", "x": "1"}, "timeout_ms": 5000}
```

## Errors
`ValueError` if `smt_lib2` is empty/whitespace, or if Z3's own parser rejects it — its message is
preserved verbatim (usually names the exact syntax position/unbound symbol), not swallowed.

**Gotcha, not an error:** declaring the same name twice with the *same* type raises
(`"already declared"`), but declaring it twice with a *different* type does not. Z3 silently shadows
the first declaration. If a generated problem reuses a variable name for a different purpose, the
result quietly uses the second declaration's type. `solve()` is a thin wrapper around Z3 and doesn't
add validation Z3 itself lacks.

## Tested limits
A 10-pigeon/9-hole pigeonhole problem (provably unsat, hard for SAT solvers) solves in about 2 seconds.
A tiny `timeout_ms` on a 13-pigeon/12-hole version returns `"unknown"` promptly, without hanging.
Expressions nested 200 levels deep and 1,000 independent constraints are both handled.

## Implementation
`tools/reasoning/solve.py`. Pure computation via the `z3-solver` package — no network, no
filesystem, no subprocess. Tests: `tests/test_reasoning_solve.py`.

## Machine-readable definition
`solve.json`, same directory — `"risk": "read"` (pure computation, no I/O).

## Related
- `local_ai.classify_complexity` — same "advisory computation" role, different domain (fuzzy
  heuristic scoring vs. exact logical satisfiability).
- `workflow.run` — can chain a `reasoning.solve` step with others.
