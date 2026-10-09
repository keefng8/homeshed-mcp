"""git.branch. See ../../capabilities/git/branch.md."""
from __future__ import annotations

import subprocess
from pathlib import Path

from registry import tool
from tools.git.status import GitError


@tool(name="branch", category="git", doc="git/branch.md")
def branch(path: str, name: str, create: bool = True, base: str | None = None) -> dict:
    """Create and/or switch to a git branch. No confirmation step — a real, immediate write, by
    design: once switched on, it runs without a dry_run gate.

    Args:
        path: directory inside a git repository.
        name: branch name to create/switch to.
        create: if true, create `name` if it doesn't already exist (`git checkout -b`); if
            false, only switch to an existing branch (`git checkout`), erroring if it's missing.
        base: ref to branch from when creating (defaults to the current HEAD).

    Returns:
        {"branch": str, "created": bool, "previous_branch": str}

    Raises:
        GitError: path invalid/not a repo, name empty, branch already exists when create=True and
            it wasn't already checked out, branch missing when create=False, or git failed.
    """
    repo_path = Path(path)
    if not repo_path.is_dir():
        raise GitError(f"not a directory: {path}")
    if not name or not name.strip():
        raise GitError("name must be non-empty")

    try:
        prev_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError("git rev-parse timed out") from None
    if prev_result.returncode != 0:
        raise GitError(f"not a git repository: {path}")
    previous_branch = prev_result.stdout.strip()

    exists_result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=repo_path, timeout=10,
    )
    already_exists = exists_result.returncode == 0

    if not create and not already_exists:
        raise GitError(f"branch does not exist: {name}")

    if already_exists:
        cmd = ["git", "checkout", name]
    else:
        cmd = ["git", "checkout", "-b", name] + ([base] if base else [])

    try:
        result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise GitError("git checkout timed out") from None
    if result.returncode != 0:
        raise GitError(f"git checkout failed: {result.stderr.strip()[:200]}")

    return {
        "branch": name,
        "created": not already_exists,
        "previous_branch": previous_branch,
    }
