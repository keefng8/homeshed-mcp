"""reasoning.delegate. See ../../capabilities/reasoning/delegate.md.

Closes the loop from reasoning.route's advisory-only recommendation to a real answer -- so simple
work is done locally. This is a genuine, narrow crossing into automatic execution, scoped safely
(see the capability doc) rather than pretending it isn't a crossing at all. Only ever auto-executes the LOCAL path; the
"claude" path never fakes an answer.
"""
from __future__ import annotations

from registry import tool
from tools.local_ai.ask import LocalAIError, ask
from tools.reasoning.route import route


MAX_BACKGROUND = 20_000  # not "context": the tool server reserves that name


@tool(name="delegate", category="reasoning", doc="reasoning/delegate.md")
def delegate(task: str, token_estimate: int = 0, tool_count: int = 0, background: str = "") -> dict:
    """Route a task and, if the local model is recommended, actually ask it. If Claude is
    recommended, never fakes an answer -- reports the routing decision so the caller (normally
    Claude) picks the task up directly.

    Args:
        task: the task/request to route and possibly answer.
        token_estimate: rough token count of the full context, if known -- passed through to
            reasoning.route, see its own docs for why this matters for accuracy.
        tool_count: number of tools available/likely invoked, if known.
        background: standing instructions, rules or background the local model should read, but that must not
            decide where the task goes (2026-10-07: an app whose agents carry a rule saying "never output a secret
            (key, token, password)" in every agent's preamble sent all their easy tasks to Claude). Routing reads
            only `task`; the local model gets background then task; it counts toward token_estimate when that is 0.

    Returns:
        {"handled_by": "local", "answer": str, "model": str, "route": {...}} if routed local and
        the local model answered successfully.
        {"handled_by": "claude", "route": {...}} if routed to Claude, OR if routed local but the
        local backend failed -- a failed local call falls back to "claude", never an error, since
        Claude picking up the task is always a safe outcome.
        `route` is always the full `reasoning.route` result, so the caller can see why the
        decision was made, not just what it was.

    Raises:
        Nothing beyond reasoning.route's own argument-type errors -- a local backend failure is
        caught and treated as a "claude" recommendation, not re-raised.
    """
    background = (background or "").strip()
    if len(background) > MAX_BACKGROUND:
        raise ValueError(f"background must be at most {MAX_BACKGROUND} characters")
    if background and not token_estimate:
        token_estimate = (len(background) + len(task)) // 4
    routing = route(text=task, token_estimate=token_estimate, tool_count=tool_count)

    if routing["recommendation"] != "local":
        return {"handled_by": "claude", "route": routing}

    try:
        prompt = "\n\n".join(p for p in (background, task) if p)
        result = ask(prompt, max_tokens=1024, temperature=0.2)
    except LocalAIError as exc:
        return {"handled_by": "claude", "route": routing, "note": f"local model unavailable: {exc}"}

    return {
        "handled_by": "local",
        "answer": result["text"],
        "model": result["model"],
        "route": routing,
    }
