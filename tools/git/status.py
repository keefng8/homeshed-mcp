"""git.status. See ../../capabilities/git/status.md."""
from __future__ import annotations

import subprocess
from pathlib import Path

from registry import tool


class GitError(RuntimeError):
    """Any git-related failure — not a repo, git not installed, path doesn't exist."""


def _parse_branch_line(line: str) -> tuple[str, str | None, int, int]:
    # "## main...origin/main [ahead 1, behind 2]" / "## main" (no upstream) /
    # "## HEAD (no branch)" (detached).
    content = line[3:]
    ahead = behind = 0
    upstream = None

    if "[" in content:
        content, tracking = content.split(" [", 1)
        tracking = tracking.rstrip("]")
        for part in tracking.split(", "):
            if part.startswith("ahead "):
                ahead = int(part.removeprefix("ahead "))
            elif part.startswith("behind "):
                behind = int(part.removeprefix("behind "))

    if "..." in content:
        branch, upstream = content.split("...", 1)
    else:
        branch = content

    return branch, upstream, ahead, behind


@tool(name="status", category="git", doc="git/status.md")
def status(path: str) -> dict:
    """Get the status of a git repository: current branch, ahead/behind tracking, and
    staged/unstaged/untracked files. Read-only — never stages, commits, or modifies anything.

    Args:
        path: directory to check. Must be inside a git repository.

    Returns:
        {branch, upstream, ahead, behind, staged: [str], unstaged: [str], untracked: [str]}

    Raises:
        GitError: path doesn't exist, isn't a directory, isn't a git repository, or git isn't
            installed.
    """
    repo_path = Path(path)
    if not repo_path.is_dir():
        raise GitError(f"not a directory: {path}")

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-b"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError("git status timed out") from None

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not a git repository" in stderr.lower():
            raise GitError(f"not a git repository: {path}")
        raise GitError(f"git status failed: {stderr[:200]}")

    lines = result.stdout.splitlines()
    if not lines:
        raise GitError("git status returned no output")

    branch, upstream, ahead, behind = _parse_branch_line(lines[0])

    staged, unstaged, untracked = [], [], []
    for line in lines[1:]:
        if len(line) < 3:
            continue
        x, y, rest = line[0], line[1], line[3:]
        if x == "?" and y == "?":
            untracked.append(rest)
            continue
        if x not in (" ", "?"):
            staged.append(rest)
        if y not in (" ", "?"):
            unstaged.append(rest)

    return {
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
    }
