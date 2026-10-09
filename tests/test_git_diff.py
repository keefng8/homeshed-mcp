"""Tests for git.diff. Real temp git repo, same rationale as test_git_status.py — git is
deterministic, not worth mocking away.

First drafted by the local model (Qwen3-8B, thinking disabled) — never imported `diff` itself
(every test would NameError), re-did the full repo-setup boilerplate in every test instead of a
shared fixture, and `test_discovered_by_registry` built a whole unused repo by copy-paste.
Rewritten using the same `repo` fixture as test_git_status.py; kept the draft's test-case list,
it was the right coverage, just badly implemented.
"""
import subprocess

import pytest


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _run(["git", "init"], tmp_path)
    _run(["git", "config", "user.email", "test@test.com"], tmp_path)
    _run(["git", "config", "user.name", "test"], tmp_path)
    (tmp_path / "committed.txt").write_text("original\n")
    _run(["git", "add", "committed.txt"], tmp_path)
    _run(["git", "commit", "-m", "initial"], tmp_path)
    return tmp_path


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "git" and c.name == "diff"]
    assert len(matches) == 1


def test_no_changes(repo):
    from tools.git.diff import diff

    result = diff(str(repo))
    assert result == {"diff": "", "truncated": False}


def test_modified_file_shows_in_diff(repo):
    from tools.git.diff import diff

    (repo / "committed.txt").write_text("modified\n")
    result = diff(str(repo))
    assert "modified" in result["diff"]
    assert "-original" in result["diff"]


def test_staged_vs_unstaged(repo):
    from tools.git.diff import diff

    (repo / "committed.txt").write_text("modified\n")

    unstaged = diff(str(repo), staged=False)
    staged = diff(str(repo), staged=True)
    assert "modified" in unstaged["diff"]
    assert staged["diff"] == ""

    _run(["git", "add", "committed.txt"], repo)

    unstaged_after_add = diff(str(repo), staged=False)
    staged_after_add = diff(str(repo), staged=True)
    assert unstaged_after_add["diff"] == ""
    assert "modified" in staged_after_add["diff"]


def test_file_filter(repo):
    from tools.git.diff import diff

    (repo / "other.txt").write_text("new\n")
    _run(["git", "add", "other.txt"], repo)
    _run(["git", "commit", "-m", "add other"], repo)
    (repo / "committed.txt").write_text("modified\n")
    (repo / "other.txt").write_text("also modified\n")

    result = diff(str(repo), file="committed.txt")
    assert "committed.txt" in result["diff"]
    assert "other.txt" not in result["diff"]


def test_invalid_context_lines_rejected(repo):
    from tools.git.diff import diff
    from tools.git.status import GitError

    with pytest.raises(GitError):
        diff(str(repo), context_lines=-1)
    with pytest.raises(GitError):
        diff(str(repo), context_lines=21)


def test_not_a_directory():
    from tools.git.diff import diff
    from tools.git.status import GitError

    with pytest.raises(GitError, match="not a directory"):
        diff("/definitely/does/not/exist")


def test_not_a_git_repo(outside_any_repo):
    from tools.git.diff import diff
    from tools.git.status import GitError

    with pytest.raises(GitError, match="not a git repository"):  # conftest.py's outside_any_repo
        diff(str(outside_any_repo))
