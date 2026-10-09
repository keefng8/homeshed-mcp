"""git.pull. See ../../capabilities/git/pull.md."""
from __future__ import annotations

import subprocess
from pathlib import Path

from registry import tool
from tools.git.status import GitError


@tool(name="pull", category="git", doc="git/pull.md")
def pull(path: str, remote: str = "origin", branch: str | None = None) -> dict:
    """Pull changes into the current branch. No confirmation step — a real, immediate write, by
    design: once switched on, it runs without a dry_run gate.

    Args:
        path: directory inside a git repository.
        remote: remote name to pull from.
        branch: remote branch to pull (default: the current branch's configured upstream).

    Returns:
        {"branch": str, "summary": str (git's own one-line update summary, e.g. "Fast-forward"
        or "Already up to date.")}

    Raises:
        GitError: path invalid/not a repo, or the pull failed (conflict, no upstream configured,
            network error, etc — the underlying git stderr is included, truncated).
    """
    repo_path = Path(path)
    if not repo_path.is_dir():
        raise GitError(f"not a directory: {path}")

    cmd = ["git", "pull", remote]
    if branch:
        cmd.append(branch)

    try:
        result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError("git pull timed out") from None
    if result.returncode != 0:
        raise GitError(f"git pull failed: {result.stderr.strip()[:300]}")

    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise GitError("pull succeeded but a follow-up git query timed out") from None

    summary = (result.stdout.strip() or result.stderr.strip()).splitlines()[-1] if (result.stdout.strip() or result.stderr.strip()) else ""
    return {"branch": branch_result.stdout.strip(), "summary": summary}
