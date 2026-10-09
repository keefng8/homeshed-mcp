from __future__ import annotations

import pytest

from tools.reasoning.solve import solve


def test_sat_problem_returns_a_satisfying_model():
    out = solve(
        "(declare-const x Int) (declare-const y Int) "
        "(assert (> x 0)) (assert (= (+ x y) 10)) (check-sat)"
    )
    assert out["result"] == "sat"
    assert out["model"] is not None
    x = int(out["model"]["x"])
    y = int(out["model"]["y"])
    assert x > 0
    assert x + y == 10


def test_unsat_problem_returns_no_model():
    out = solve("(declare-const x Int) (assert (> x 0)) (assert (< x 0)) (check-sat)")
    assert out["result"] == "unsat"
    assert out["model"] is None


def test_empty_input_raises():
    with pytest.raises(ValueError, match="non-empty"):
        solve("")


def test_whitespace_only_input_raises():
    with pytest.raises(ValueError, match="non-empty"):
        solve("   \n\t  ")


def test_malformed_smt2_raises_with_z3s_own_message():
    with pytest.raises(ValueError, match="invalid SMT-LIB2"):
        solve("this is not valid smt2 at all")


def test_timeout_is_clamped_to_max_not_rejected():
    out = solve("(declare-const x Int) (assert (> x 0)) (check-sat)", timeout_ms=999_999)
    assert out["timeout_ms"] == 60_000


def test_timeout_is_clamped_to_minimum_not_rejected():
    out = solve("(declare-const x Int) (assert (> x 0)) (check-sat)", timeout_ms=-5)
    assert out["timeout_ms"] == 1


def test_default_timeout_reported_in_result():
    out = solve("(declare-const x Int) (assert (> x 0)) (check-sat)")
    assert out["timeout_ms"] == 5000


def test_problem_with_no_free_variables_still_solves():
    out = solve("(assert true) (check-sat)")
    assert out["result"] == "sat"


# Stress tests: hard, timeout, deep and wide problems.
# Both close a real gap: every test above either finished instantly or only checked that
# timeout_ms gets CLAMPED -- none actually exercised a real timeout firing, and none used a
# problem hard enough to meaningfully test the solver rather than trivial arithmetic.

def _pigeonhole_unsat_smt(n_pigeons: int, n_holes: int) -> str:
    """Classic hard-for-SAT-solvers unsatisfiable problem: n pigeons into n-1 holes, no two
    pigeons in the same hole. Provably unsat, but proving it isn't trivial -- a real stress test,
    not just an arithmetic toy."""
    decls = " ".join(f"(declare-const p_{i}_{j} Bool)" for i in range(n_pigeons) for j in range(n_holes))
    each_pigeon_one_hole = " ".join(
        "(assert (or " + " ".join(f"p_{i}_{j}" for j in range(n_holes)) + "))" for i in range(n_pigeons)
    )
    no_two_share_a_hole = " ".join(
        f"(assert (not (and p_{i1}_{j} p_{i2}_{j})))"
        for j in range(n_holes)
        for i1 in range(n_pigeons)
        for i2 in range(i1 + 1, n_pigeons)
    )
    return f"{decls} {each_pigeon_one_hole} {no_two_share_a_hole} (check-sat)"


def test_genuinely_hard_problem_still_solves_correctly_within_timeout():
    # 10 pigeons/9 holes -- large enough to be a real stress test, small enough to solve well
    # inside the default budget. Verified live taking ~2s against a 3s timeout before being
    # committed as a test with a safely larger 15s budget (CI/slower hardware headroom).
    out = solve(_pigeonhole_unsat_smt(10, 9), timeout_ms=15_000)
    assert out["result"] == "unsat"
    assert out["model"] is None


def test_genuine_timeout_returns_unknown_not_a_crash_or_hang():
    # 13 pigeons/12 holes with a deliberately tiny timeout -- forces a REAL timeout, not just
    # tests that the timeout_ms parameter gets clamped. Confirmed live this returns promptly
    # (~0.05s, not hanging until some other limit) with "unknown", never raises.
    out = solve(_pigeonhole_unsat_smt(13, 12), timeout_ms=50)
    assert out["result"] == "unknown"
    assert out["model"] is None


def test_deeply_nested_expression_does_not_crash():
    nested = "(declare-const x Int) (assert (= x " + "(+ 1 " * 200 + "0" + ")" * 200 + ")) (check-sat)"
    out = solve(nested)
    assert out["result"] == "sat"


def test_redeclaring_a_name_with_a_different_type_silently_shadows_not_an_error():
    # Real finding, 2026-09-24: assumed this would raise (a genuine mistake someone could make
    # by reusing a variable name) -- it doesn't. Z3 silently shadows: the second declaration
    # wins, the first is simply gone, no error, no warning. Not a solve() bug -- this is Z3's
    # own SMT-LIB2 parser behavior, confirmed live before writing this test to match reality
    # instead of assuming. Documented as a real gotcha in solve.md, not "fixed" -- solve() is a
    # thin, honest wrapper around Z3 and shouldn't add validation Z3 itself doesn't have.
    out = solve("(declare-const x Int) (declare-const x Bool) (check-sat)")
    assert out["result"] == "sat"


def test_many_independent_constraints_scale_without_issue():
    huge = (
        " ".join(f"(declare-const v{i} Int)" for i in range(1000))
        + " "
        + " ".join(f"(assert (> v{i} 0))" for i in range(1000))
        + " (check-sat)"
    )
    out = solve(huge, timeout_ms=5000)
    assert out["result"] == "sat"
