"""git.commit. See ../../capabilities/git/commit.md."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from registry import tool
from tools.git.status import GitError

_LOCK_RETRY_ATTEMPTS = 3
_LOCK_RETRY_DELAY_S = 0.3


def _run_with_lock_retry(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    """Real scenario found live 2026-09-24: concurrent git.commit calls against the same repo
    (a genuine possibility for a network-reachable capability) hit git's own .git/index.lock
    contention -- one of three simultaneous commits failed cleanly, no corruption, fully
    recoverable by retrying. That's git behaving correctly (protecting its own index), but a
    caller shouldn't have to know to retry a lock error themselves. Retries only on the specific
    lock-contention error text, not on any other failure (a real merge conflict or bad pathspec
    should still surface immediately, not be silently retried into a confusing state).
    """
    last_result = None
    for attempt in range(_LOCK_RETRY_ATTEMPTS):
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0 or "index.lock" not in result.stderr:
            return result
        last_result = result
        if attempt < _LOCK_RETRY_ATTEMPTS - 1:
            time.sleep(_LOCK_RETRY_DELAY_S)
    return last_result


@tool(name="commit", category="git", doc="git/commit.md")
def commit(path: str, message: str, files: list[str] | None = None, add_all: bool = False) -> dict:
    """Stage and create a git commit. No confirmation step — a real, immediate write, by design
    (once switched on, it runs without a dry_run gate).

    Args:
        path: directory inside a git repository.
        message: commit message. Must be non-empty.
        files: explicit list of paths (relative to `path`) to stage before committing. Ignored
            if `add_all` is true.
        add_all: stage every tracked change (`git add -A`) instead of an explicit file list.
            Must pass either `files` or `add_all=True` — never guesses which changes to include.

    Returns:
        {"commit": str (short hash), "branch": str, "files_committed": [str]}

    Raises:
        GitError: path invalid/not a repo, message empty, neither files nor add_all given,
            nothing staged to commit, or the underlying git command failed.
    """
    repo_path = Path(path)
    if not repo_path.is_dir():
        raise GitError(f"not a directory: {path}")
    if not message or not message.strip():
        raise GitError("message must be non-empty")
    if not files and not add_all:
        raise GitError("must specify either files or add_all=True — never guesses what to stage")

    add_cmd = ["git", "add", "-A"] if add_all else ["git", "add", "--", *files]
    try:
        add_result = _run_with_lock_retry(add_cmd, repo_path, timeout=30)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError("git add timed out") from None
    if add_result.returncode != 0:
        raise GitError(f"git add failed: {add_result.stderr.strip()[:200]}")

    try:
        commit_result = _run_with_lock_retry(["git", "commit", "-m", message], repo_path, timeout=30)
    except subprocess.TimeoutExpired:
        raise GitError("git commit timed out") from None
    if commit_result.returncode != 0:
        stderr = commit_result.stderr.strip()
        if "nothing to commit" in commit_result.stdout.lower() or "nothing to commit" in stderr.lower():
            raise GitError("nothing staged to commit")
        raise GitError(f"git commit failed: {stderr[:200]}")

    try:
        hash_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        files_result = subprocess.run(
            # --root: without it, diff-tree shows nothing for a repo's first-ever commit (no
            # parent to diff against) -- found live 2026-09-24, files_committed came back empty
            # for a real first commit despite the commit itself succeeding correctly.
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise GitError("commit succeeded but a follow-up git query timed out") from None

    return {
        "commit": hash_result.stdout.strip(),
        "branch": branch_result.stdout.strip(),
        "files_committed": [f for f in files_result.stdout.splitlines() if f],
    }
