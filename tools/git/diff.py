"""git.diff. See ../../capabilities/git/diff.md."""
from __future__ import annotations

import subprocess
from pathlib import Path

from registry import tool
from tools.git.status import GitError

MAX_DIFF_CHARS = 50_000


@tool(name="diff", category="git", doc="git/diff.md")
def diff(path: str, staged: bool = False, file: str | None = None, context_lines: int = 3) -> dict:
    """Show a unified diff for a git repository. Read-only — never stages or commits anything.

    Args:
        path: directory inside (or root of) a git repository.
        staged: diff the staged/index changes instead of the working tree.
        file: restrict the diff to one file, relative to the repo root. Omit for the whole repo.
        context_lines: lines of context around each change, 0-20.

    Returns:
        {diff: str, truncated: bool} — diff is empty string if there are no changes. truncated
        is true if the real diff exceeded 50,000 chars and was cut off.

    Raises:
        GitError: same failure modes as git.status (not a directory, not a repo, git missing).
    """
    repo_path = Path(path)
    if not repo_path.is_dir():
        raise GitError(f"not a directory: {path}")
    if not 0 <= context_lines <= 20:
        raise GitError("context_lines must be between 0 and 20")

    args = ["git", "diff", f"-U{context_lines}"]
    if staged:
        args.append("--cached")
    if file:
        args.extend(["--", file])

    try:
        result = subprocess.run(args, cwd=repo_path, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError("git diff timed out") from None

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not a git repository" in stderr.lower():
            raise GitError(f"not a git repository: {path}")
        raise GitError(f"git diff failed: {stderr[:200]}")

    text = result.stdout
    truncated = len(text) > MAX_DIFF_CHARS
    if truncated:
        text = text[:MAX_DIFF_CHARS]

    return {"diff": text, "truncated": truncated}
