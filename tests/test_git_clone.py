"""Tests for git.clone. Clones a real local repo -- no network dependency in CI."""
import subprocess

import pytest


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def source_repo(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    _run(["git", "init"], src)
    _run(["git", "config", "user.email", "test@test.com"], src)
    _run(["git", "config", "user.name", "test"], src)
    (src / "f.txt").write_text("v1")
    _run(["git", "add", "f.txt"], src)
    _run(["git", "commit", "-m", "initial"], src)
    return src


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "git" and c.name == "clone"]
    assert len(matches) == 1


def test_clone_local_repo(tmp_path, source_repo):
    from tools.git.clone import clone

    dest = tmp_path / "cloned"
    result = clone(str(source_repo), str(dest))
    assert result["destination"] == str(dest)
    assert (dest / "f.txt").exists()


def test_clone_into_existing_destination_raises(tmp_path, source_repo):
    from tools.git.clone import GitError, clone

    dest = tmp_path / "already-here"
    dest.mkdir()
    with pytest.raises(GitError, match="already exists"):
        clone(str(source_repo), str(dest))


def test_empty_url_raises(tmp_path):
    from tools.git.clone import GitError, clone

    with pytest.raises(GitError, match="non-empty"):
        clone("", str(tmp_path / "dest"))


def test_bad_url_raises(tmp_path):
    from tools.git.clone import GitError, clone

    with pytest.raises(GitError, match="git clone failed"):
        clone("/definitely/does/not/exist/repo", str(tmp_path / "dest"))
