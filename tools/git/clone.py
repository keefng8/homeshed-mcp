"""git.clone. See ../../capabilities/git/clone.md."""
from __future__ import annotations

import subprocess
from pathlib import Path

from registry import tool
from tools.git.status import GitError


@tool(name="clone", category="git", doc="git/clone.md")
def clone(url: str, destination: str, branch: str | None = None) -> dict:
    """Clone a git repository. No confirmation step — a real, immediate write, by design
    (once switched on, it runs without a dry_run gate).

    Args:
        url: repository URL (any scheme `git clone` itself accepts — https/ssh/local path).
        destination: target directory. Must not already exist.
        branch: specific branch to clone (default: the remote's default branch).

    Returns:
        {"destination": str, "branch": str}

    Raises:
        GitError: url empty, destination already exists, or the clone failed (bad URL, auth
            failure, network error, etc — the underlying git stderr is included, truncated).
    """
    if not url or not url.strip():
        raise GitError("url must be non-empty")
    dest_path = Path(destination)
    if dest_path.exists():
        raise GitError(f"destination already exists: {destination}")

    cmd = ["git", "clone"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, str(dest_path)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError("git clone timed out") from None
    if result.returncode != 0:
        raise GitError(f"git clone failed: {result.stderr.strip()[:300]}")

    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=dest_path, capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise GitError("clone succeeded but a follow-up git query timed out") from None

    return {"destination": str(dest_path), "branch": branch_result.stdout.strip()}
