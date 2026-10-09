"""Tests for git.commit. Real temp git repo, not mocked (matches test_git_status.py's convention)."""
import subprocess
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

    matches = [c for c in discover() if c.category == "git" and c.name == "commit"]
    assert len(matches) == 1


def test_commit_explicit_files(repo):
    from tools.git.commit import commit

    (repo / "new.txt").write_text("hi")
    result = commit(str(repo), "add new.txt", files=["new.txt"])
    assert result["files_committed"] == ["new.txt"]
    assert result["branch"]
    assert len(result["commit"]) >= 7


def test_first_commit_in_fresh_repo_reports_files(tmp_path):
    # Regression test for a real bug found live 2026-09-24: git diff-tree needs --root for a
    # repo's very first commit (no parent to diff against otherwise) -- files_committed came
    # back empty for a real first commit despite the commit itself succeeding. The `repo` fixture
    # above always has a prior commit already, so it never exercised this path.
    from tools.git.commit import commit

    _run(["git", "init"], tmp_path)
    _run(["git", "config", "user.email", "test@test.com"], tmp_path)
    _run(["git", "config", "user.name", "test"], tmp_path)
    (tmp_path / "f.txt").write_text("v1")

    result = commit(str(tmp_path), "first commit", add_all=True)
    assert result["files_committed"] == ["f.txt"]


def test_commit_add_all(repo):
    from tools.git.commit import commit

    (repo / "a.txt").write_text("a")
    (repo / "b.txt").write_text("b")
    result = commit(str(repo), "add both", add_all=True)
    assert set(result["files_committed"]) == {"a.txt", "b.txt"}


def test_neither_files_nor_add_all_raises(repo):
    from tools.git.commit import GitError, commit

    with pytest.raises(GitError, match="add_all"):
        commit(str(repo), "message")


def test_empty_message_raises(repo):
    from tools.git.commit import GitError, commit

    with pytest.raises(GitError, match="non-empty"):
        commit(str(repo), "", add_all=True)


def test_nothing_to_commit_raises(repo):
    from tools.git.commit import GitError, commit

    with pytest.raises(GitError, match="nothing staged"):
        commit(str(repo), "no-op", add_all=True)


def test_not_a_directory():
    from tools.git.commit import GitError, commit

    with pytest.raises(GitError, match="not a directory"):
        commit("/definitely/does/not/exist", "msg", add_all=True)


def test_git_not_installed(repo):
    from tools.git.commit import GitError, commit

    with patch("tools.git.commit.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(GitError, match="not installed"):
            commit(str(repo), "msg", add_all=True)


def test_lock_contention_retries_then_succeeds(repo):
    # Regression test for a real scenario found live 2026-09-24: concurrent git.commit calls on
    # the same repo hit .git/index.lock contention. One of three simultaneous real calls failed
    # cleanly (no corruption, confirmed via git.status afterward) -- this test proves the retry
    # now recovers automatically instead of requiring the caller to retry themselves.
    from tools.git.commit import commit

    (repo / "new.txt").write_text("hi")
    lock_error = MagicMock(returncode=128, stderr="fatal: Unable to create '.../.git/index.lock': File exists.")
    real_success = MagicMock(returncode=0, stderr="")

    call_count = {"n": 0}
    real_run = subprocess.run

    def flaky_run(cmd, **kwargs):
        if cmd[:2] == ["git", "add"]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return lock_error
        return real_run(cmd, **kwargs)

    with patch("tools.git.commit.time.sleep"):  # don't actually wait in tests
        with patch("tools.git.commit.subprocess.run", side_effect=flaky_run):
            result = commit(str(repo), "add new.txt", files=["new.txt"])

    assert call_count["n"] == 2  # failed once, retried, succeeded
    assert result["files_committed"] == ["new.txt"]


def test_non_lock_failure_not_retried(repo):
    # A real error (bad pathspec) must surface immediately, not be silently retried.
    from tools.git.commit import GitError, commit

    with pytest.raises(GitError, match="did not match any files"):
        commit(str(repo), "msg", files=["definitely-does-not-exist.txt"])
