"""reasoning.solve. See ../../capabilities/reasoning/solve.md.

Wraps Z3 (Microsoft's SMT solver) directly -- no LLM call, no execution of arbitrary code. The
input is standard SMT-LIB2 text (Z3's own native format, not a project-specific DSL), per this
project's own "use standard protocols, don't reinvent the wheel" principle -- and per
ARCHITECTURE.md's considered decision to never build arbitrary code execution: SMT-LIB2 is
declarative (it describes a logic problem, it can't read a file, open a socket, or run a shell
command), so parsing it carries none of the risk `dev.*` execution would.
"""
from __future__ import annotations

import z3

from registry import tool

_MAX_TIMEOUT_MS = 60_000


@tool(name="solve", category="reasoning", doc="reasoning/solve.md")
def solve(smt_lib2: str, timeout_ms: int = 5000) -> dict:
    """Solve a constraint-satisfaction/logic problem expressed in standard SMT-LIB2 text.

    Use this for questions with a real logical structure -- "is this set of constraints
    internally consistent," "does this scheduling/configuration have a valid assignment,"
    "find values satisfying these conditions" -- not as a general reasoning engine. If the
    problem doesn't reduce to a satisfiability question, this isn't the right tool for it.

    Args:
        smt_lib2: standard SMT-LIB2 source, e.g.
            "(declare-const x Int) (assert (> x 0)) (check-sat)". Must include at least one
            `(check-sat)` command -- z3 only reports a result for commands actually present in
            the source, it doesn't infer one.
        timeout_ms: solver time budget, 1-60000ms (default 5000). Clamped, not rejected, if out
            of range -- a caller asking for too much patience gets the max, not an error.

    Returns:
        {"result": "sat"|"unsat"|"unknown", "model": {var: value, ...} | None, "timeout_ms": int}
        `model` is only populated when `result == "sat"` -- one satisfying assignment, not every
        possible one. `"unknown"` means the timeout was hit before Z3 could decide either way,
        not that the problem is malformed.

    Raises:
        ValueError if `smt_lib2` is empty/whitespace-only, or if Z3 can't parse it (its own
        parse error message is preserved, not swallowed -- it's usually specific enough to fix
        the input directly, e.g. naming the exact unbound symbol or syntax position).
    """
    if not smt_lib2 or not smt_lib2.strip():
        raise ValueError("smt_lib2 must be non-empty")

    timeout_ms = max(1, min(timeout_ms, _MAX_TIMEOUT_MS))

    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    try:
        solver.from_string(smt_lib2)
    except z3.Z3Exception as exc:
        raise ValueError(f"invalid SMT-LIB2: {exc}") from exc

    verdict = solver.check()
    model = None
    if verdict == z3.sat:
        m = solver.model()
        model = {str(decl): str(m[decl]) for decl in m.decls()}

    return {"result": str(verdict), "model": model, "timeout_ms": timeout_ms}
