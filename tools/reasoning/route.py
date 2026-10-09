"""reasoning.route. See ../../capabilities/reasoning/route.md.

ADVISORY ONLY -- recommends local model vs. Claude for a task. Never executes anything, never
intercepts a real request, never redirects a caller anywhere. This is a read-only signal an
already-in-the-loop decider (Claude, deciding whether to delegate to local_ai.ask; a human,
looking at the dashboard) can choose to use or ignore. See
local-ai-operating-rules.md's "Advisory routing vs. automatic routing" note for exactly why this
doesn't violate that file's standing "no automatic routing yet" rule -- that rule is about a
system silently redirecting execution with no one in the loop; this has no execution path at all.

Optional Shedkeeper second opinion (needs SHEDKEEPER_URL): a confident "claude" from Shedkeeper (SHEDKEEPER_UPGRADE or more) upgrades a heuristic "local"; it never
downgrades a "claude" decision. Each call is logged as numbers only (ROUTE_LOG) so the threshold can be calibrated.
Shedkeeper missing or slow = the heuristic alone, as before.

Only borderline cases ask it (checked against a real route log): a heuristic "local"
with a judgement word, or scoring SHEDKEEPER_FROM or more. Of 232 logged calls, all asked Shedkeeper (~0.4 s each), 36 were already
"claude" (where it can change nothing), and only 8 were upgraded, all scoring 8-14. The band asks on about a fifth and
keeps 5 of the 8. The 3 it drops had no judgement word and p 0.75-0.80, the coin-flip band for a zero-shot model. The
question goes to Shedkeeper named "route", so a Shedkeeper feed can mark the answers it ignores as "no effect".

Off by default since 2026-09-30 (R&D's re-measure, R34): with the borderline band it was asked on 16% of 166 calls and
changed none (median p 0.60, never 0.75), so it only added latency. ROUTE_ASK_SHEDKEEPER=1 asks it again; decide and assess
still use Shedkeeper either way.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from registry import tool
from tools.local_ai.classify_complexity import classify_complexity
from paths import data_path

# Starting point, not a tuned value -- same honesty as classify_complexity's own docs about its
# own unvalidated accuracy. Revisit once this project has real usage data to calibrate against.
_LOCAL_THRESHOLD = 35
# Judgement work (2026-09-29): every request all night scored 5-18 and routed "local", even a credentials vault and a
# security fix, so the recommendation was ignored. Two or more distinct judgement words route to Claude; one adds
# weight. Grunt parts are named either way, so Claude still hands them to the local model.
_JUDGEMENT = re.compile(r"\b(?:secur\w*|credential\w*|secrets?|vault|encrypt\w*|passwords?|auth\w*|permission\w*|"
                        r"architect\w*|migrat\w*|deploy\w*|production|refactor\w*|rotat\w*|incident|root[- ]cause|"
                        r"schema|data loss)\b", re.I)
_GRUNT = re.compile(r"\b(?:summar\w*|drafts?|test lists?|boilerplate|docstrings?|translat\w*|renam\w*|"
                    r"classif\w*|write (?:the )?(?:docs?|tests?|guide))\b", re.I)
_JUDGEMENT_WEIGHT = 20
SHEDKEEPER_UPGRADE = 0.75
SHEDKEEPER_FROM = 25  # a heuristic "local" scoring this or more (up to the threshold) is borderline enough to ask Shedkeeper
SHEDKEEPER_CRITERIA = {"local": "simple summarising, drafting, listing, formatting or boilerplate work",
                 "claude": "security, architecture, credentials, debugging or judgement-heavy work"}
ROUTE_LOG = Path(os.environ.get("ROUTE_LOG_FILE") or data_path("usage/route_calibration.jsonl"))


def _ask_shedkeeper() -> bool:
    return os.environ.get("ROUTE_ASK_SHEDKEEPER") == "1"


def _shedkeeper_opinion(text: str) -> dict | None:
    """Shedkeeper's fast choice between the two, or None when Shedkeeper isn't configured or doesn't answer in time."""
    try:
        from tools.local_ai import decide as decide_module
    except ImportError:  # the public copy has no Shedkeeper (a companion service)
        return None
    url = decide_module._shedkeeper_url()
    if not url:
        return None
    try:
        out = decide_module._shedkeeper_choice(url, f"Task: {(text or '')[:600]}", "Choose who should do this task.",
                                         SHEDKEEPER_CRITERIA, name="route")
    except decide_module.DecideError:
        return None
    return {"choice": out["choice"], "p": round(out["probabilities"][out["choice"]], 3)}


def _log(**row) -> None:
    """Numbers only (never the request text), for calibrating the Shedkeeper threshold. Never raises."""
    try:
        ROUTE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ROUTE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time()), **row}) + "\n")
    except OSError:
        pass


def _finish(out: dict, heuristic: str, shedkeeper: dict | None, scored: dict, judgement: list, grunt: list) -> dict:
    """Apply Shedkeeper's upgrade rule, attach its opinion and log the call."""
    if shedkeeper:
        out["shedkeeper"] = shedkeeper
        if out["recommendation"] == "local" and shedkeeper["choice"] == "claude" and shedkeeper["p"] >= SHEDKEEPER_UPGRADE:
            out["recommendation"], out["confidence"] = "claude", "medium"
            out["reasoning"] += f"; Shedkeeper: judgement work (p={shedkeeper['p']}), so Claude leads"
    _log(score=scored["score"], judgement=len(judgement), grunt=len(grunt), heuristic=heuristic,
         shedkeeper=(shedkeeper or {}).get("choice"), p=(shedkeeper or {}).get("p"), final=out["recommendation"])
    return out


@tool(name="route", category="reasoning", doc="reasoning/route.md")
def route(text: str, token_estimate: int = 0, tool_count: int = 0) -> dict:
    """Recommend whether a task should go to the local model or Claude. Combines
    classify_complexity's score with its explicit force_local/force_cloud signals, a judgement-word signal, and
    Shedkeeper's fast second opinion.

    Args:
        text: the request text to route.
        token_estimate: rough token count of the full context, if known.
        tool_count: number of tools available/likely invoked, if known.

    Returns:
        {"recommendation": "local"|"claude", "confidence": "high"|"medium"|"low", "score": int,
         "reasoning": str, "delegate_parts"?: [grunt words], "shedkeeper"?: {"choice", "p"}}. `confidence` reflects
        distance from the threshold, not certainty about the task itself -- a score right at the boundary is
        genuinely ambiguous, and the caller should weigh that, not treat "local"/"claude" as equally trustworthy
        regardless of confidence.

    Raises:
        Nothing beyond classify_complexity's own argument-type errors.
    """
    scored = classify_complexity(text=text, token_estimate=token_estimate, tool_count=tool_count)
    judgement = sorted({m.group(0).lower() for m in _JUDGEMENT.finditer(text or "")})
    grunt = sorted({m.group(0).lower() for m in _GRUNT.finditer(text or "")})
    delegate = {"delegate_parts": grunt} if grunt else {}

    if scored["force_local"]:  # a greeting or a one-word check: not worth asking Shedkeeper
        return {"recommendation": "local", "confidence": "high", "score": scored["score"],
                "reasoning": "trivial request (greeting/acknowledgement/simple check)", **delegate}
    # Claude already leads in the next two cases, and Shedkeeper never downgrades, so it isn't asked.
    if scored["force_cloud"]:
        out = {"recommendation": "claude", "confidence": "high", "score": scored["score"],
               "reasoning": "matches a pattern that should never read as trivial regardless of "
                            "length (e.g. security audit, PR review)", **delegate}
        return _finish(out, "claude", None, scored, judgement, grunt)
    if len(judgement) >= 2:
        out = {"recommendation": "claude", "confidence": "high", "score": scored["score"],
               "reasoning": f"judgement work ({', '.join(judgement[:4])}): Claude plans and reviews; hand the grunt "
                            "parts to the local model", **delegate}
        return _finish(out, "claude", None, scored, judgement, grunt)

    score = scored["score"] + (_JUDGEMENT_WEIGHT if judgement else 0)
    distance = abs(score - _LOCAL_THRESHOLD)
    confidence = "high" if distance >= 20 else "medium" if distance >= 8 else "low"
    recommendation = "claude" if score >= _LOCAL_THRESHOLD else "local"

    parts = [
        f"{label}={scored[key]}"
        for label, key in (
            ("tokens", "token_score"),
            ("tools", "tool_score"),
            ("task_type", "task_type_score"),
            ("code", "code_complexity_score"),
            ("reasoning", "reasoning_score"),
        )
        if scored[key]
    ]
    if judgement:
        parts.append(f"judgement={_JUDGEMENT_WEIGHT} ({judgement[0]})")
    breakdown = ", ".join(parts) if parts else "no signals matched"
    out = {"recommendation": recommendation, "confidence": confidence, "score": score,
           "reasoning": f"score {score} ({breakdown}) vs threshold {_LOCAL_THRESHOLD}", **delegate}
    borderline = _ask_shedkeeper() and recommendation == "local" and (bool(judgement) or score >= SHEDKEEPER_FROM)
    return _finish(out, recommendation, _shedkeeper_opinion(text) if borderline else None, scored, judgement, grunt)
