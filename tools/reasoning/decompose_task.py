"""reasoning.decompose_task. See ../../capabilities/reasoning/decompose_task.md.

Second piece of the "local reasoning" layer, after reasoning.solve. Unlike solve (pure Z3
computation, no LLM), decomposition genuinely needs language understanding -- so this delegates
to local_ai.ask (per local-ai-operating-rules.md: local model first for exactly this kind of
tedious, well-bounded task) rather than trying to fake it with a deterministic heuristic the way
classify_complexity does. Advisory only, same as every other reasoning.*/local_ai.* capability --
it returns subtasks for a caller to act on (e.g. as workflow.run steps), it never builds or
executes a workflow itself.
"""
from __future__ import annotations

import json
import re

from registry import tool
from tools.local_ai.ask import LocalAIError, ask

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

_PROMPT_TEMPLATE = """Break the following task into a numbered sequence of concrete, ordered \
subtasks needed to complete it. Each subtask should be a short, actionable phrase.

Task: {task}

Reply with ONLY a JSON array of strings, at most {max_subtasks} items, nothing else -- no prose, \
no markdown code fences, no explanation."""


@tool(name="decompose_task", category="reasoning", doc="reasoning/decompose_task.md")
def decompose_task(task: str, max_subtasks: int = 8) -> dict:
    """Break a task description into an ordered list of subtasks, via the local model.

    Advisory only -- returns subtasks for a caller to act on (e.g. feed straight into
    workflow.run's own steps once each subtask is mapped to a real capability call), never
    builds or executes anything itself.

    Args:
        task: the task to decompose, in plain language.
        max_subtasks: upper bound on how many subtasks to ask for, 1-20 (clamped, not rejected,
            if out of range).

    Returns:
        {"task": str, "subtasks": [str, ...], "model": str}. `subtasks` preserves the order the
        model returned them in -- treat that as the suggested sequence, not guaranteed correct.

    Raises:
        ValueError if `task` is empty/whitespace-only.
        RuntimeError if the local AI backend is unavailable, or if its response couldn't be
        parsed as a JSON array of strings after one retry with a stricter reminder (the model
        occasionally wraps its answer in prose or a markdown fence despite the instruction not
        to -- one retry with an explicit correction is attempted before giving up, since a local
        7B model gets this format right often enough that outright failing on the first miss
        would be needlessly strict).
    """
    if not task or not task.strip():
        raise ValueError("task must be non-empty")

    max_subtasks = max(1, min(max_subtasks, 20))
    prompt = _PROMPT_TEMPLATE.format(task=task, max_subtasks=max_subtasks)

    subtasks, model_name = _ask_for_subtasks(prompt)
    if subtasks is None:
        # One retry with an explicit correction -- the model's own prior (wrong) reply is not
        # replayed, just a stricter instruction; keeps the retry prompt short and doesn't risk
        # the model repeating its own malformed output back.
        stricter = prompt + "\n\nYour reply must start with '[' and end with ']'. Nothing else."
        subtasks, model_name = _ask_for_subtasks(stricter)

    if subtasks is None:
        raise RuntimeError(
            "local AI backend did not return a parseable JSON array of subtasks after a retry"
        )

    return {"task": task, "subtasks": subtasks[:max_subtasks], "model": model_name}


def _ask_for_subtasks(prompt: str) -> tuple[list[str] | None, str]:
    try:
        result = ask(prompt, max_tokens=512, temperature=0.0)
    except LocalAIError as exc:
        raise RuntimeError(f"local AI backend unavailable for decomposition: {exc}") from exc

    match = _JSON_ARRAY_RE.search(result["text"])
    if not match:
        return None, result["model"]
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, result["model"]
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        return None, result["model"]
    return parsed, result["model"]
