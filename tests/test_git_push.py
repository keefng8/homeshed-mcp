"""Tests for git.push. Real local repo pair -- no network dependency in CI."""
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

    return local, remote


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "git" and c.name == "push"]
    assert len(matches) == 1


def test_push_new_commit(repo_pair):
    from tools.git.push import push

    local, remote = repo_pair
    (local / "f.txt").write_text("v2")
    _run(["git", "add", "f.txt"], local)
    _run(["git", "commit", "-m", "update"], local)

    result = push(str(local))
    assert result["remote"] == "origin"
    assert result["forced"] is False

    check = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=remote, capture_output=True, text=True
    )
    assert check.stdout.strip() == "update"


def test_not_a_directory():
    from tools.git.push import GitError, push

    with pytest.raises(GitError, match="not a directory"):
        push("/definitely/does/not/exist")
