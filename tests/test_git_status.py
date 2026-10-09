"""Tests for git.status. Uses a real temp git repo (git itself is a deterministic tool, not
something worth mocking away — matches how this capability is actually used) plus mocked
subprocess for the error paths that need a specific failure shape.
"""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _run(["git", "init"], tmp_path)
    _run(["git", "config", "user.email", "test@test.com"], tmp_path)
    _run(["git", "config", "user.name", "test"], tmp_path)
    (tmp_path / "committed.txt").write_text("v1")
    _run(["git", "add", "committed.txt"], tmp_path)
    _run(["git", "commit", "-m", "initial"], tmp_path)
    return tmp_path


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "git" and c.name == "status"]
    assert len(matches) == 1


def test_clean_repo(repo):
    from tools.git.status import status

    result = status(str(repo))
    assert result["staged"] == []
    assert result["unstaged"] == []
    assert result["untracked"] == []
    assert result["upstream"] is None


def test_untracked_file(repo):
    from tools.git.status import status

    (repo / "new.txt").write_text("hi")
    result = status(str(repo))
    assert "new.txt" in result["untracked"]


def test_staged_and_unstaged(repo):
    from tools.git.status import status

    (repo / "committed.txt").write_text("v2")
    _run(["git", "add", "committed.txt"], repo)
    (repo / "committed.txt").write_text("v3")

    result = status(str(repo))
    assert "committed.txt" in result["staged"]
    assert "committed.txt" in result["unstaged"]


def test_not_a_directory():
    from tools.git.status import GitError, status

    with pytest.raises(GitError, match="not a directory"):
        status("/definitely/does/not/exist")


def test_not_a_git_repo(outside_any_repo):
    from tools.git.status import GitError, status

    # A folder with no git repository above it (conftest.py's outside_any_repo: tmp_path on Linux, D:\ on a PC whose
    # user folder is itself a repo)
    with pytest.raises(GitError, match="not a git repository"):
        status(str(outside_any_repo))


def test_git_not_installed():
    from tools.git.status import GitError, status

    with patch("tools.git.status.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(GitError, match="not installed"):
            status(".")
