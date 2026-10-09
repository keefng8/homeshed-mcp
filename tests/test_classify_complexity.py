from __future__ import annotations

from tools.local_ai.classify_complexity import classify_complexity


def test_force_local_greeting_short_circuits_to_zero():
    out = classify_complexity(text="hello")
    assert out == {
        "score": 0, "force_local": True, "force_cloud": False,
        "token_score": 0, "tool_score": 0, "task_type_score": 0,
        "code_complexity_score": 0, "reasoning_score": 0,
    }


def test_force_local_yes_no_and_time_check_also_short_circuit():
    for text in ["yes", "OKAY.", "what time is it?", "help"]:
        assert classify_complexity(text=text)["force_local"] is True


def test_force_cloud_floors_score_at_76_even_with_low_other_scores():
    out = classify_complexity(text="please do a security audit", token_estimate=0, tool_count=0)
    assert out["force_cloud"] is True
    assert out["score"] >= 76


# Real gaps found live 2026-09-24 via a stress-test battery -- both scored "local" before the
# fix (14 and 8 respectively), a dangerous miss since both are exactly the kind of high-stakes
# task that should never read as trivial. The original Lynkr port's FORCE_CLOUD_PATTERNS matched
# only literal phrases ("security audit", "complex debug") -- these are very natural, very common
# phrasings that don't happen to use those exact words.

def test_force_cloud_catches_vulnerability_review_phrasing_not_in_original_port():
    out = classify_complexity(text="please review our auth system for vulnerabilities")
    assert out["force_cloud"] is True
    assert out["score"] >= 76


def test_force_cloud_catches_vulnerability_check_phrasing():
    out = classify_complexity(text="can you check this code for vulnerabilities")
    assert out["force_cloud"] is True


def test_force_cloud_catches_deadlock_mention_regardless_of_phrasing():
    out = classify_complexity(text="help me debug this race condition deadlock in production")
    assert out["force_cloud"] is True
    assert out["score"] >= 76


def test_force_cloud_catches_bare_deadlock_mention():
    out = classify_complexity(text="why does this deadlock")
    assert out["force_cloud"] is True


def test_force_cloud_catches_race_condition_mention():
    out = classify_complexity(text="there's a race condition somewhere in this code")
    assert out["force_cloud"] is True


def test_word_audit_alone_does_not_trigger_force_cloud():
    # "audit" appears in SECURITY_RE's keyword set but shouldn't force_cloud on its own -- only
    # when paired with a review/check/assess verb AND "vulnerab", or matching another explicit
    # pattern. A feature literally named "audit trail" is not a request to audit anything.
    out = classify_complexity(text="add audit trail logging to this feature")
    assert out["force_cloud"] is False


def test_simple_question_scores_low():
    out = classify_complexity(text="What is the capital of France?")
    assert out["force_local"] is False
    assert out["task_type_score"] == 3
    assert out["score"] < 25  # should land in a SIMPLE-tier-equivalent range


def test_refactor_keyword_beats_multi_file_in_the_original_elif_order():
    # REFACTOR_RE is checked before MULTI_FILE_RE in the original if/elif chain -- a text
    # matching both gets REFACTOR_RE's score (16), not MULTI_FILE_RE's (22). Asserting this
    # explicitly since it's an easy thing to get backwards when porting an if/elif chain.
    text = "Refactor the entire codebase to a microservice architecture with a clean design pattern"
    out = classify_complexity(text=text, token_estimate=6000, tool_count=12)
    assert out["task_type_score"] == 16
    assert out["code_complexity_score"] > 0
    assert out["score"] > 50


def test_multi_file_wins_when_refactor_keyword_is_absent():
    text = "Update every file across the entire architecture with a new design pattern"
    out = classify_complexity(text=text)
    assert out["task_type_score"] == 22


def test_reasoning_keywords_add_to_reasoning_score():
    out = classify_complexity(text="Let's think through the trade-offs step by step")
    assert out["reasoning_score"] == 4


# --- Exact boundary values against Lynkr's original Rust match-statement bands ---
# (native/src/lib.rs: match token_estimate { 0..500 => 0, 500..1000 => 4, 1000..2000 => 8,
#  2000..4000 => 12, 4000..8000 => 16, _ => 20 })

def test_token_score_boundaries():
    cases = [(0, 0), (499, 0), (500, 4), (999, 4), (1000, 8), (1999, 8),
             (2000, 12), (3999, 12), (4000, 16), (7999, 16), (8000, 20), (50000, 20)]
    for token_estimate, expected in cases:
        out = classify_complexity(text="a generic non-matching request", token_estimate=token_estimate)
        assert out["token_score"] == expected, f"token_estimate={token_estimate}"


# (native/src/lib.rs: match tool_count { 0 => 0, 1..=3 => 4, 4..=6 => 8, 7..=10 => 12,
#  11..=15 => 16, _ => 20 })

def test_tool_score_boundaries():
    cases = [(0, 0), (1, 4), (3, 4), (4, 8), (6, 8), (7, 12), (10, 12),
              (11, 16), (15, 16), (16, 20), (100, 20)]
    for tool_count, expected in cases:
        out = classify_complexity(text="a generic non-matching request", tool_count=tool_count)
        assert out["tool_score"] == expected, f"tool_count={tool_count}"


def test_code_complexity_score_caps_at_20():
    text = ("all files across the entire architecture design pattern security audit "
            "concurrent async performance optimize database sql query")
    out = classify_complexity(text=text)
    assert out["code_complexity_score"] == 20


def test_empty_text_does_not_crash_and_is_not_force_local():
    out = classify_complexity(text="")
    assert out["score"] >= 0
    assert out["force_local"] is False
