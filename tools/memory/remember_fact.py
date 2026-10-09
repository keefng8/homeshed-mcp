"""memory.remember_fact. See ../../capabilities/memory/remember_fact.md."""
from __future__ import annotations

import projects
from registry import tool
from tools.memory.capture import DEFAULT_FACT_CATEGORY, capture, facts_session_id


@tool(name="remember_fact", category="memory", doc="memory/remember_fact.md")
def remember_fact(content: str, category: str = DEFAULT_FACT_CATEGORY, project_dir: str = "",
                  scope: str = "project") -> dict:
    """Write a durable, high-value fact about the project into the dedicated facts tier of
    persistent memory — distinct from memory.capture's general-purpose, topic-scoped storage.

    Use this for facts that should outrank everything else on recall: architecture decisions,
    current deployment topology, standing constraints, real capability counts. Do NOT use this
    for implementation detail, bug postmortems, or anything that will be superseded within days —
    use memory.capture with a topic-specific session_id for that instead, so it doesn't crowd out
    the facts tier. See memory.capture.md's "Facts tier vs. topic sessions" for the full
    convention.

    Facts are append-only (no update/delete) — when a fact changes, restate it fully rather than
    describing the change ("mcp-server has 16 capabilities", not "added 2 capabilities").
    memory.recall_facts returns newest-first, so the current restatement naturally surfaces above
    superseded ones within the same category.

    Args:
        content: the fact to remember. Max 16,000 chars, same limit as memory.capture.
        category: which separate facts log this belongs to (e.g. "mcp-server", "memory-stack",
            "local-ai", "known-gaps", "conventions"). Lowercase letters/digits/hyphens, 1-40
            chars. Defaults to "general" — the original, pre-category bucket. Pick an existing
            category when the fact fits one (see memory.recall_facts.md for categories already in
            use); only introduce a new one when nothing existing fits.
        project_dir: the caller's working folder; picks that project's own memory (projects.py).
        scope: "project" (default) or "global" (the shared memory). An app's "global" fact waits for the owner's
            approval on the web panel, as memory.capture's does.

    Returns:
        {"accepted": bool, "message_id": str | None}

    Raises:
        MemoryError: same failure modes as memory.capture, plus an invalid category.
    """
    with projects.working_in(project_dir):
        stored = capture(facts_session_id(category), content, role="user", scope=scope)
        if not stored.get("pending"):  # a waiting fact is indexed when the owner approves it (memory_pending.py)
            _index(category, scope)
    return stored


def _index(category: str, scope: str = "project") -> None:
    """Record a new category so memory.recall_relevant searches it too. Best effort: a failure never loses the fact."""
    from tools.memory.recall_relevant import FACT_INDEX, fact_categories
    try:
        if category not in fact_categories(scope):
            capture(facts_session_id(FACT_INDEX), category, role="user", scope=scope)
    except Exception:  # noqa: BLE001 - the fact itself is already saved
        pass
