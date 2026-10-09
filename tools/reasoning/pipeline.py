"""reasoning.pipeline. See ../../capabilities/reasoning/pipeline.md.

The full diagram, in one call: USER/TASK -> LOCAL REASONING (route) -> "local or claude"
(delegate) -> SHARED MEMORY (memory.capture). The reasoning.* tools (solve, decompose_task, route, delegate) are each callable on
their own; this is the one pipeline that chains them end to end, built directly from the already-reviewed, already-tested pieces rather than a new
monolithic implementation.
"""
from __future__ import annotations

from registry import tool
from tools.memory.capture import MemoryError, capture
from tools.reasoning.delegate import delegate

_MEMORY_SESSION_ID = "reasoning-pipeline-log"


@tool(name="pipeline", category="reasoning", doc="reasoning/pipeline.md")
def pipeline(task: str, token_estimate: int = 0, tool_count: int = 0, remember: bool = True) -> dict:
    """Run the full local-reasoning pipeline: route the task, delegate to the local model when
    recommended (never faking an answer for the Claude path), then persist a record of the
    outcome to shared memory. This is `reasoning.delegate` plus the diagram's final arrow.

    Args:
        task: the task/request to process.
        token_estimate: passed through to reasoning.route (via delegate) -- see its docs.
        tool_count: passed through to reasoning.route (via delegate).
        remember: whether to persist the outcome to memory (default True). Set False for a dry
            run of the pipeline that doesn't touch memory at all.

    Returns:
        Everything `reasoning.delegate` returns (`handled_by`, `answer`/`note`, `route`), plus
        `{"remembered": bool, "message_id": str | None}`. A memory-write failure never hides the
        actual task outcome -- `remembered` is False and `memory_error` names the failure, but
        `handled_by`/`answer`/`route` are always the real result.

    Raises:
        Nothing beyond reasoning.delegate's own argument-type errors. A memory-write failure is
        caught and reported, not re-raised -- the task was still genuinely handled either way.
    """
    result = delegate(task=task, token_estimate=token_estimate, tool_count=tool_count)

    if not remember:
        return {**result, "remembered": False, "message_id": None}

    summary_lines = [f"Task: {task}", f"Handled by: {result['handled_by']}"]
    if result["handled_by"] == "local":
        summary_lines.append(f"Answer: {result['answer']}")
    else:
        summary_lines.append(f"Reason: {result['route']['reasoning']}")
    summary = "\n".join(summary_lines)

    try:
        mem_result = capture(_MEMORY_SESSION_ID, summary, role="user")
    except MemoryError as exc:
        return {**result, "remembered": False, "message_id": None, "memory_error": str(exc)}

    return {
        **result,
        "remembered": bool(mem_result.get("accepted")),
        "message_id": mem_result.get("message_id"),
    }
