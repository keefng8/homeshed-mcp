"""Tests for git.pull. Real local repo pair -- no network dependency in CI."""
import subprocess

import pytest


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo_pair(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    _run(["git", "init", "--bare"], remote)

    local = tmp_path / "local"
    local.mkdir()
    _run(["git", "init"], local)
    _run(["git", "config", "user.email", "test@test.com"], local)
    _run(["git", "config", "user.name", "test"], local)
    (local / "f.txt").write_text("v1")
    _run(["git", "add", "f.txt"], local)
    _run(["git", "commit", "-m", "initial"], local)
    _run(["git", "remote", "add", "origin", str(remote)], local)
    _run(["git", "push", "-u", "origin", "HEAD"], local)

    other = tmp_path / "other"
    _run(["git", "clone", str(remote), str(other)], tmp_path)
    _run(["git", "config", "user.email", "test@test.com"], other)
    _run(["git", "config", "user.name", "test"], other)
    (other / "f.txt").write_text("v2")
    _run(["git", "add", "f.txt"], other)
    _run(["git", "commit", "-m", "update from other"], other)
    _run(["git", "push"], other)

    return local


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "git" and c.name == "pull"]
    assert len(matches) == 1


def test_pull_fast_forward(repo_pair):
    from tools.git.pull import pull

    result = pull(str(repo_pair))
    assert result["branch"]
    assert (repo_pair / "f.txt").read_text() == "v2"


def test_not_a_directory():
    from tools.git.pull import GitError, pull

    with pytest.raises(GitError, match="not a directory"):
        pull("/definitely/does/not/exist")
