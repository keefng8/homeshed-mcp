"""Tests for files.list. Runs for real against a temp directory tree -- same reasoning as
test_dns_lookup.py/test_system_info.py: a thin filesystem wrapper is more honestly tested
against the real filesystem than mocked.
"""
import os

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "files" and c.name == "list"]
    assert len(matches) == 1


@pytest.fixture(autouse=True)
def readable(tmp_path, monkeypatch):
    """files.list reads only inside READ_ALLOWED_ROOTS (readroots.py, 2026-09-30): here, the test's temp folder."""
    monkeypatch.setenv("READ_ALLOWED_ROOTS", str(tmp_path))
    return tmp_path


def test_outside_the_allowed_folders_is_refused(tmp_path, monkeypatch):
    from tools.files.list import FilesListError, list_dir

    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.setenv("READ_ALLOWED_ROOTS", str(inside))
    assert list_dir(str(inside))["entries"] == []
    with pytest.raises(FilesListError, match="outside the folders"):
        list_dir(str(tmp_path))
    with pytest.raises(FilesListError, match="outside the folders"):
        list_dir(str(inside / ".." / ".."))


@pytest.fixture
def tmp_tree(tmp_path):
    (tmp_path / "b_file.txt").write_text("hello")
    (tmp_path / "a_dir").mkdir()
    (tmp_path / "z_file.txt").write_text("world!!")
    return tmp_path


def test_lists_files_and_dirs(tmp_tree):
    from tools.files.list import list_dir

    result = list_dir(str(tmp_tree), detail="full")
    names = [e["name"] for e in result["entries"]]
    assert set(names) == {"b_file.txt", "a_dir", "z_file.txt"}


def test_dirs_sorted_before_files(tmp_tree):
    from tools.files.list import list_dir

    result = list_dir(str(tmp_tree), detail="full")
    types_in_order = [e["type"] for e in result["entries"]]
    assert types_in_order[0] == "dir"


def test_alphabetical_within_type(tmp_tree):
    from tools.files.list import list_dir

    result = list_dir(str(tmp_tree), detail="full")
    file_names = [e["name"] for e in result["entries"] if e["type"] == "file"]
    assert file_names == sorted(file_names, key=str.lower)


def test_size_and_type_are_correct(tmp_tree):
    from tools.files.list import list_dir

    result = list_dir(str(tmp_tree), detail="full")
    by_name = {e["name"]: e for e in result["entries"]}
    assert by_name["b_file.txt"]["size_bytes"] == 5
    assert by_name["b_file.txt"]["type"] == "file"
    assert by_name["a_dir"]["type"] == "dir"


def test_permissions_reported_for_real_file(tmp_tree):
    from tools.files.list import list_dir

    result = list_dir(str(tmp_tree), detail="full")
    by_name = {e["name"]: e for e in result["entries"]}
    perms = by_name["b_file.txt"]["permissions"]
    assert len(perms["octal"]) == 3
    assert perms["octal"].isdigit()
    # human form always starts with a type char (- for regular file, d for dir, etc.)
    assert perms["human"][0] in "-dlpsc"
    assert len(perms["human"]) == 10  # e.g. "-rw-r--r--"


def test_returns_resolved_absolute_path(tmp_tree):
    from tools.files.list import list_dir

    result = list_dir(str(tmp_tree), detail="full")
    assert os.path.isabs(result["path"])


def test_rejects_nonexistent_path(tmp_path):
    from tools.files.list import FilesListError, list_dir

    with pytest.raises(FilesListError, match="does not exist"):
        list_dir(str(tmp_path / "does-not-exist"))


def test_rejects_file_as_path(tmp_tree):
    from tools.files.list import FilesListError, list_dir

    with pytest.raises(FilesListError, match="not a directory"):
        list_dir(str(tmp_tree / "b_file.txt"))


def test_default_path_is_the_first_allowed_folder(readable, monkeypatch):
    """"." is the first allowed folder: READ_ALLOWED_ROOTS, else CLAUDE_PROJECT_DIR, else the working folder."""
    from pathlib import Path

    from tools.files.list import list_dir

    assert list_dir()["path"] == str(Path(readable).resolve())
    monkeypatch.delenv("READ_ALLOWED_ROOTS")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert list_dir()["path"] == str(Path(os.getcwd()).resolve())


# --- the default cap (2026-09-30: 300 files came back as 50K characters). Test list drafted by the local model. ---

def _folder(root, files, dirs=0):
    for i in range(dirs):
        (root / f"dir{i:03}").mkdir()
    for i in range(files):
        (root / f"file{i:03}.txt").write_text("x")
    return str(root)


def test_a_big_folder_is_capped_at_100_and_says_how_many_were_left_out(tmp_path):
    from tools.files.list import list_dir

    out = list_dir(_folder(tmp_path, files=105, dirs=2), detail="full")
    assert out["total"] == 107 and len(out["entries"]) == 100 and out["more"] == 7
    assert [e["type"] for e in out["entries"][:3]] == ["dir", "dir", "file"]  # still directories first


def test_limit_raises_or_lowers_the_cap(tmp_path):
    from tools.files.list import list_dir

    path = _folder(tmp_path, files=5)
    few = list_dir(path, limit=2, detail="full")
    assert [e["name"] for e in few["entries"]] == ["file000.txt", "file001.txt"] and few["more"] == 3
    everything = list_dir(path, limit=5)
    assert len(everything["entries"]) == everything["total"] == 5 and "more" not in everything


def test_an_empty_folder_says_total_0(tmp_path):
    from tools.files.list import list_dir

    assert list_dir(str(tmp_path)) == {"path": str(tmp_path.resolve()), "total": 0, "entries": []}


@pytest.mark.parametrize("bad", [0, 1001, -1, True, 2.5])
def test_limit_outside_1_to_1000_is_refused(tmp_path, bad):
    from tools.files.list import FilesListError, list_dir

    with pytest.raises(FilesListError, match="limit"):
        list_dir(str(tmp_path), limit=bad)


# --- compact by default (2026-09-30): ls -F style strings, about a tenth of the full size ---

def test_compact_entries_read_like_ls(tmp_tree):
    from tools.files.list import list_dir

    out = list_dir(str(tmp_tree))
    assert out["entries"] == ["a_dir/", "b_file.txt (5 B)", "z_file.txt (7 B)"] and out["total"] == 3


def test_compact_sizes_are_human(tmp_path):
    from tools.files.list import list_dir

    (tmp_path / "empty.txt").write_text("")
    (tmp_path / "kb.bin").write_bytes(b"x" * 1536)
    with open(tmp_path / "mb.bin", "wb") as f:
        f.truncate(3 * 1024 * 1024)
    assert list_dir(str(tmp_path))["entries"] == ["empty.txt (0 B)", "kb.bin (1.5 KB)", "mb.bin (3.0 MB)"]


def test_a_symlink_is_marked_with_at(tmp_path):
    from tools.files.list import list_dir

    (tmp_path / "real.txt").write_text("x")
    try:
        os.symlink(tmp_path / "real.txt", tmp_path / "link")
    except OSError:
        pytest.skip("this machine can't make symlinks without extra rights")
    assert "link@" in list_dir(str(tmp_path))["entries"]


@pytest.mark.parametrize("bad", ["", "summary", "FULL"])
def test_detail_must_be_compact_or_full(tmp_path, bad):
    from tools.files.list import FilesListError, list_dir

    with pytest.raises(FilesListError, match="detail"):
        list_dir(str(tmp_path), detail=bad)
