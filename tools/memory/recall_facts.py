"""memory.recall_facts. See ../../capabilities/memory/recall_facts.md."""
from __future__ import annotations

from registry import tool
from tools.memory.capture import DEFAULT_FACT_CATEGORY, facts_session_id
from tools.memory.recall import recall

# Facts are meant to be read back in full, as a small standing knowledge base — not paginated
# conversation history. Lower than a generous "give me everything" number on purpose: categories
# (2026-09-21) already scope each recall to one topic, so the point of this default is "enough to
# see the current state of this category," not "every fact ever recorded" — keep it small and
# fast to read, raise it explicitly if a category genuinely needs more.
DEFAULT_LIMIT = 20


@tool(name="recall_facts", category="memory", doc="memory/recall_facts.md")
def recall_facts(category: str = DEFAULT_FACT_CATEGORY, limit: int = DEFAULT_LIMIT, max_chars: int = 300,
                 scope: str = "project", project_dir: str = "") -> dict:
    """Read back one category of the project's durable facts tier — everything saved with
    memory.remember_fact under that category. Scoped to one category by design: pulling every
    fact ever recorded defeats the point of a fast, clear facts tier — call this once per category
    actually relevant to the current task instead of dumping everything.

    Results are newest-first, so a superseded restatement of a fact (memory.remember_fact is
    append-only) sits below its current version rather than requiring a separate lookup.

    Args:
        category: which facts log to read (e.g. "mcp-server", "memory-stack", "local-ai",
            "known-gaps", "conventions"). Defaults to "general" — the original, pre-category
            bucket. Known categories in use, 2026-09-21: general, platform, mcp-server,
            memory-stack, local-ai, known-gaps, conventions — see memory.remember_fact.md.
        limit: max facts to return, 1-100 (memory.recall's own range). Defaults to 20 — enough to
            see a category's current state without pulling its entire history.
        max_chars: cut each fact to this many characters (0 = full text). Defaults to 300, the
            per-request memory check's size (it was 0 until 2026-09-29: R&D found recall_facts put
            out 1.87M characters in four days, 8.1K a call).
        scope: "project" (default) reads this project's own facts; "global" reads the shared
            platform facts (homelab-wide knowledge every project may need), whichever client asks.
        project_dir: the caller's working folder; picks that project's own memory (projects.py).

    Returns:
        {"messages": [{"id", "role", "content", "timestamp"}], "total": int} — same shape as
        memory.recall.

    Raises:
        MemoryError: same failure modes as memory.recall, plus an invalid category or scope.
    """
    return recall(facts_session_id(category), limit=limit, max_chars=max_chars, scope=scope, project_dir=project_dir)
