"""Tests for git.branch. Real temp git repo, not mocked."""
import subprocess

import pytest


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _run(["git", "init"], tmp_path)
    _run(["git", "config", "user.email", "test@test.com"], tmp_path)
    _run(["git", "config", "user.name", "test"], tmp_path)
    (tmp_path / "f.txt").write_text("v1")
    _run(["git", "add", "f.txt"], tmp_path)
    _run(["git", "commit", "-m", "initial"], tmp_path)
    return tmp_path


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "git" and c.name == "branch"]
    assert len(matches) == 1


def test_create_new_branch(repo):
    from tools.git.branch import branch

    result = branch(str(repo), "feature-x")
    assert result == {"branch": "feature-x", "created": True, "previous_branch": result["previous_branch"]}
    assert result["previous_branch"] in ("main", "master")


def test_switch_to_existing_branch(repo):
    from tools.git.branch import branch

    created = branch(str(repo), "feature-x")
    branch(str(repo), created["previous_branch"])
    result = branch(str(repo), "feature-x", create=False)
    assert result["created"] is False
    assert result["branch"] == "feature-x"


def test_switch_to_missing_branch_without_create_raises(repo):
    from tools.git.branch import GitError, branch

    with pytest.raises(GitError, match="does not exist"):
        branch(str(repo), "nope", create=False)


def test_empty_name_raises(repo):
    from tools.git.branch import GitError, branch

    with pytest.raises(GitError, match="non-empty"):
        branch(str(repo), "")


def test_not_a_directory():
    from tools.git.branch import GitError, branch

    with pytest.raises(GitError, match="not a directory"):
        branch("/definitely/does/not/exist", "x")
