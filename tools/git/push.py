"""git.push. See ../../capabilities/git/push.md."""
from __future__ import annotations

import subprocess
from pathlib import Path

from registry import tool
from tools.git.status import GitError


@tool(name="push", category="git", doc="git/push.md")
def push(path: str, remote: str = "origin", branch: str | None = None, force: bool = False) -> dict:
    """Push the current branch to a remote. No confirmation step — a real, immediate write, by
    design: once switched on, it runs without a dry_run gate. `force`
    defaults false; passing true does a real force-push (`--force-with-lease`, not a bare
    `--force`, so it still refuses if the remote has commits this push doesn't know about).

    Args:
        path: directory inside a git repository.
        remote: remote name to push to.
        branch: branch to push (default: the current branch).
        force: use `--force-with-lease` instead of a plain push.

    Returns:
        {"branch": str, "remote": str, "forced": bool}

    Raises:
        GitError: path invalid/not a repo, or the push failed (rejected, auth failure, network
            error, lease mismatch, etc — the underlying git stderr is included, truncated).
    """
    repo_path = Path(path)
    if not repo_path.is_dir():
        raise GitError(f"not a directory: {path}")

    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError("git rev-parse timed out") from None
    if branch_result.returncode != 0:
        raise GitError(f"not a git repository: {path}")
    target_branch = branch or branch_result.stdout.strip()

    cmd = ["git", "push"]
    if force:
        cmd.append("--force-with-lease")
    cmd += [remote, target_branch]

    try:
        result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise GitError("git push timed out") from None
    if result.returncode != 0:
        raise GitError(f"git push failed: {result.stderr.strip()[:300]}")

    return {"branch": target_branch, "remote": remote, "forced": force}
