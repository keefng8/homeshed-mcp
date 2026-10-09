"""workflow.run — chain existing capabilities into one multi-step task. See
../../capabilities/workflow/run.md.
"""
from __future__ import annotations

import re

from registry import get_capability, tool

_REF_PATTERN = re.compile(r"^\$([a-zA-Z_][a-zA-Z0-9_]*)((?:\.[a-zA-Z0-9_]+)*)$")


def _resolve_ref(ref: str, results_by_id: dict[str, dict]) -> object:
    match = _REF_PATTERN.match(ref)
    if not match:
        return ref  # not a reference -- a literal string that happens to start with $
    step_id, path = match.group(1), match.group(2)
    if step_id not in results_by_id:
        raise ValueError(f"argument references unknown or not-yet-run step id '{step_id}'")
    value = results_by_id[step_id]
    for key in path.lstrip(".").split(".") if path else []:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"'${step_id}{path}' does not exist in step '{step_id}'s result")
        value = value[key]
    return value


def _substitute(value: object, results_by_id: dict[str, dict]) -> object:
    if isinstance(value, str) and value.startswith("$"):
        return _resolve_ref(value, results_by_id)
    if isinstance(value, dict):
        return {k: _substitute(v, results_by_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, results_by_id) for v in value]
    return value


@tool(name="run", category="workflow", doc="workflow/run.md")
def run(steps: list[dict]) -> dict:
    """Run an ordered list of existing capabilities as one workflow, stopping at the first
    failure. This chains capabilities that already exist — it does not and never will gain its
    own build/deploy/exec powers (see capabilities/workflow/run.md's "What this deliberately
    does not do").

    Args:
        steps: non-empty list of {"capability": "<category.name>", "arguments": {...},
            "id": "<optional step name>"}. "arguments" is optional (defaults to {}). "id" is
            optional — set it to let a LATER step's arguments reference this step's result via
            "$<id>" (the whole result dict) or "$<id>.some.nested.key" (a specific value inside
            it), anywhere in that later step's arguments, including nested inside dicts/lists.
            Every step is resolved against the real registry, and every "$id" reference is
            checked against earlier steps' ids, BEFORE any step runs — an unknown capability id
            or a reference to a step id that doesn't exist (or comes later in the list) fails the
            whole call up front, never partway through. A literal string that happens to start
            with "$" but isn't a valid "$id" or "$id.path" reference is passed through unchanged.

    Returns:
        {"ok": bool, "completed": int, "total": int, "steps": [...]}. Each entry in "steps" is
        {"capability", "ok": True, "result": <the capability's own return value>} on success, or
        {"capability", "ok": False, "error": str(exception)} on failure — and is always the LAST
        entry, since execution stops there. Write-risk capabilities keep their own dry_run
        default (usually True) unless a step's own "arguments" explicitly overrides it — this
        function passes arguments straight through (after resolving any "$id" references), it
        never adds or removes a dry_run value itself.

    Raises:
        ValueError if steps is empty, a step isn't a dict with a "capability" key, a step
        references a capability id that doesn't exist, or an argument references a step id that
        doesn't exist or hasn't run yet by that point in the list — always before any step runs.
        A "$id.path" reference whose path doesn't exist in that step's actual result raises at
        the point that step's result is used, since the result isn't known until then.
    """
    if not steps:
        raise ValueError("steps must be a non-empty list")

    resolved = []
    seen_ids: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "capability" not in step:
            raise ValueError(f"step {i} must be a dict with a 'capability' key")
        capability_id = step["capability"]
        capability = get_capability(capability_id)
        if capability is None:
            raise ValueError(f"step {i}: unknown capability '{capability_id}'")

        step_id = step.get("id")
        arguments = step.get("arguments") or {}
        for ref in re.findall(r"\$[a-zA-Z_][a-zA-Z0-9_.]*", repr(arguments)):
            match = _REF_PATTERN.match(ref)
            if match and match.group(1) not in seen_ids:
                raise ValueError(
                    f"step {i}: argument references step id '{match.group(1)}' which doesn't "
                    "exist or comes later in the list — a step can only reference an earlier one"
                )
        if step_id:
            seen_ids.add(step_id)

        resolved.append((capability_id, step_id, capability.func, arguments))

    step_results = []
    results_by_id: dict[str, dict] = {}
    for capability_id, step_id, func, arguments in resolved:
        try:
            substituted = _substitute(arguments, results_by_id)
            result = func(**substituted)
        except Exception as exc:
            step_results.append({"capability": capability_id, "ok": False, "error": str(exc)})
            break
        step_results.append({"capability": capability_id, "ok": True, "result": result})
        if step_id:
            results_by_id[step_id] = result

    return {
        "ok": len(step_results) == len(steps) and all(r["ok"] for r in step_results),
        "completed": len(step_results),
        "total": len(steps),
        "steps": step_results,
    }
