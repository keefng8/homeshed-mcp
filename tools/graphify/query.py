"""graphify.query. See ../../capabilities/graphify/query.md."""
from __future__ import annotations

import subprocess
from pathlib import Path

from registry import tool

MAX_QUESTION_CHARS = 1000
MIN_BUDGET = 100
MAX_BUDGET = 20_000


class GraphifyError(RuntimeError):
    """Any graphify CLI failure — binary not found, graph missing, or the command itself failed."""


@tool(name="query", category="graphify", doc="graphify/query.md")
def query(project_path: str, question: str, budget: int = 2000, dfs: bool = False) -> dict:
    """Ask graphify's already-generated code graph a question. LOCATOR, not an answerer — returns
    `src=` paths ranked by term overlap; read those files for the actual answer, don't treat this
    output as a final answer on its own (see .claude/local-ai-operating-rules.md §11 and
    CLAUDE.md's "graphify is a LOCATOR" reading rule).

    Args:
        project_path: directory containing a `graphify-out/graph.json` (i.e. a project graphify
            has already been run against — this does not generate one).
        question: natural-language question, max 1000 chars.
        budget: max output tokens, 100-20000 (CLI default 2000).
        dfs: use depth-first instead of breadth-first traversal.

    Returns:
        {"result": str, "graph_path": str} — result is graphify's raw text output (node list,
        community labels, truncation notice if the budget was too small).

    Raises:
        GraphifyError: `graphify` isn't on PATH, no graphify-out/graph.json under project_path,
            question empty/too long, budget out of range, or the CLI itself failed.
    """
    if not question or not question.strip():
        raise GraphifyError("question must be non-empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise GraphifyError(f"question too long ({len(question)} chars, max {MAX_QUESTION_CHARS})")
    if not MIN_BUDGET <= budget <= MAX_BUDGET:
        raise GraphifyError(f"budget must be between {MIN_BUDGET} and {MAX_BUDGET}")

    graph_path = Path(project_path) / "graphify-out" / "graph.json"
    if not graph_path.is_file():
        raise GraphifyError(f"no graph found at {graph_path} — run graphify against this project first")

    cmd = ["graphify", "query", question, "--graph", str(graph_path), "--budget", str(budget)]
    if dfs:
        cmd.append("--dfs")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise GraphifyError("graphify is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GraphifyError("graphify query timed out") from None

    if result.returncode != 0:
        raise GraphifyError(f"graphify query failed: {result.stderr.strip()[:300]}")

    return {"result": result.stdout.strip(), "graph_path": str(graph_path)}
