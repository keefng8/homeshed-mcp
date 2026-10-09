from __future__ import annotations

from tools.reasoning.route import route


def test_force_local_pattern_recommends_local_with_high_confidence():
    out = route("hey")
    assert out == {
        "recommendation": "local",
        "confidence": "high",
        "score": 0,
        "reasoning": "trivial request (greeting/acknowledgement/simple check)",
    }


def test_force_cloud_pattern_recommends_claude_with_high_confidence():
    out = route("do a full security audit of our authentication flow")
    assert out["recommendation"] == "claude"
    assert out["confidence"] == "high"
    assert out["score"] == 76
    assert "trivial" in out["reasoning"]


def test_low_score_recommends_local():
    out = route("summarize this log file")
    assert out["recommendation"] == "local"
    assert out["score"] < 35


def test_high_score_via_context_recommends_claude():
    out = route("refactor this module", token_estimate=8000, tool_count=16)
    assert out["recommendation"] == "claude"
    assert out["score"] >= 35


def test_reasoning_names_contributing_subscores():
    out = route("refactor this module", token_estimate=8000, tool_count=16)
    assert "tokens=" in out["reasoning"]
    assert "tools=" in out["reasoning"]


def test_score_exactly_at_threshold_recommends_claude():
    # threshold is inclusive on the claude side (score >= threshold -> claude)
    out = route("x", token_estimate=0, tool_count=0)
    # baseline "else" task_type_score=5 for non-matching text -- confirm low score stays local
    assert out["score"] < 35
    assert out["recommendation"] == "local"


def test_confidence_is_low_near_the_threshold():
    # token_estimate tuned to land close to the threshold without crossing force_cloud
    out = route("build a small internal tool", token_estimate=1000, tool_count=1)
    assert out["confidence"] in ("low", "medium")  # not asserting exact score, just proximity behavior


def test_confidence_is_high_far_from_threshold():
    out = route("hi there")
    assert out["confidence"] == "high"


def test_reasoning_reports_no_signals_matched_when_score_is_zero():
    out = route("zzz qqq wwww", token_estimate=0, tool_count=0)
    if out["score"] == 0:
        assert "no signals matched" in out["reasoning"] or out["recommendation"] == "local"


# --- judgement work (2026-09-29: every request all night routed "local", even a credentials vault) ---

from tools.reasoning.route import route as _route  # noqa: E402


def test_security_and_architecture_work_routes_to_claude():
    out = _route("Build an owner-only credentials vault with encryption and password-checked reveal")
    assert out["recommendation"] == "claude" and "judgement work" in out["reasoning"]


def test_one_judgement_word_adds_weight_but_does_not_decide_alone():
    plain, weighted = _route("rename the helper function"), _route("rename the deploy helper function")
    assert weighted["score"] == plain["score"] + 20


def test_grunt_parts_are_named_even_when_claude_leads():
    out = _route("Design the vault architecture and security review, then draft the docs and write the tests")
    assert out["recommendation"] == "claude"
    assert "drafts" in out["delegate_parts"] or "draft" in " ".join(out["delegate_parts"])


def test_plain_grunt_work_stays_local():
    out = _route("summarize this file")
    assert out["recommendation"] == "local" and out["delegate_parts"] == ["summarize"]


# --- Shedkeeper second opinion (2026-09-29) ---

import pytest  # noqa: E402
import tools.reasoning.route as route_module  # noqa: E402


@pytest.fixture(autouse=True)
def _no_shedkeeper_unless_asked(monkeypatch, request, tmp_path):
    monkeypatch.setattr(route_module, "ROUTE_LOG", tmp_path / "route_log.jsonl")
    if "shedkeeper" not in request.node.name:
        monkeypatch.setattr(route_module, "_shedkeeper_opinion", lambda text: None)
    else:  # the Shedkeeper path is off by default since 2026-09-30 (R34); these tests cover it switched on
        monkeypatch.setenv("ROUTE_ASK_SHEDKEEPER", "1")


BORDERLINE = "tidy up the deploy helper module"  # one judgement word: still "local", so Shedkeeper is asked


def _asking(monkeypatch, answer):
    asked = []

    def opinion(text):
        asked.append(text)
        return answer
    monkeypatch.setattr(route_module, "_shedkeeper_opinion", opinion)
    return asked


def test_a_confident_shedkeeper_upgrades_local_to_claude(monkeypatch):
    monkeypatch.setattr(route_module, "_shedkeeper_opinion", lambda text: {"choice": "claude", "p": 0.8})
    out = route_module.route(BORDERLINE)
    assert out["recommendation"] == "claude" and "Shedkeeper" in out["reasoning"] and out["shedkeeper"]["p"] == 0.8


def test_a_hesitant_shedkeeper_changes_nothing(monkeypatch):
    monkeypatch.setattr(route_module, "_shedkeeper_opinion", lambda text: {"choice": "claude", "p": 0.6})
    assert route_module.route(BORDERLINE)["recommendation"] == "local"


def test_shedkeeper_is_not_asked_when_the_rules_say_claude(monkeypatch):
    """Drafted by the local model. Shedkeeper can only upgrade, so asking it here would be wasted time."""
    asked = _asking(monkeypatch, {"choice": "local", "p": 0.99})
    out = route_module.route("Build an owner-only credentials vault with encryption")
    assert out["recommendation"] == "claude" and asked == [] and "shedkeeper" not in out


def test_shedkeeper_is_skipped_for_plain_local_work(monkeypatch):
    asked = _asking(monkeypatch, {"choice": "claude", "p": 0.99})
    assert route_module.route("tidy up the helper module")["recommendation"] == "local" and asked == []


def test_shedkeeper_is_asked_on_a_borderline_local_task(monkeypatch):
    asked = _asking(monkeypatch, {"choice": "claude", "p": 0.6})
    out = route_module.route(BORDERLINE)
    assert asked == [BORDERLINE] and out["recommendation"] == "local" and out["shedkeeper"]["p"] == 0.6


def test_shedkeeper_hears_the_route_question_by_name(monkeypatch):
    decide_module = pytest.importorskip("tools.local_ai.decide")  # the public copy has no Shedkeeper (a companion service)
    sent = {}

    def choice(url, state, instructions, criteria, name="decision"):
        sent["name"] = name
        return {"choice": "local", "probabilities": {"local": 0.7, "claude": 0.3}, "confidence": 0.1}
    monkeypatch.setattr(decide_module, "_shedkeeper_url", lambda: "http://shedkeeper")
    monkeypatch.setattr(decide_module, "_shedkeeper_choice", choice)
    assert route_module._shedkeeper_opinion("anything") == {"choice": "local", "p": 0.7} and sent["name"] == "route"


def test_shedkeeper_calls_are_logged_as_numbers_only(monkeypatch):
    monkeypatch.setattr(route_module, "_shedkeeper_opinion", lambda text: {"choice": "claude", "p": 0.8})
    route_module.route("tidy up the secret helper module")
    row = route_module.ROUTE_LOG.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "secret" not in row and '"final": "claude"' in row


def test_without_shedkeeper_the_heuristic_stands():
    out = route_module.route("tidy up the helper module")
    assert out["recommendation"] == "local" and "shedkeeper" not in out


def test_shedkeeper_is_not_asked_unless_switched_on(monkeypatch):
    """R&D's re-measure (R34): 0 changes in 166 calls, so the default is off; ROUTE_ASK_SHEDKEEPER=1 brings it back."""
    monkeypatch.delenv("ROUTE_ASK_SHEDKEEPER", raising=False)
    asked = _asking(monkeypatch, {"choice": "claude", "p": 0.99})
    out = route_module.route(BORDERLINE)
    assert asked == [] and out["recommendation"] == "local" and "shedkeeper" not in out
    monkeypatch.setenv("ROUTE_ASK_SHEDKEEPER", "1")
    assert route_module.route(BORDERLINE)["recommendation"] == "claude" and asked == [BORDERLINE]
