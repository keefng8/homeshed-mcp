"""repo.leak_scan: private details and secrets reported as file, line and pattern, never the matched text. The fake
secrets are built at run time so this file itself doesn't trip the commit scanner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.repo.leak_scan import LeakScanError, leak_scan

TOKEN = "gh" + "p_" + "A" * 36
PASSWORD_LINE = "pass" + 'word = "correct-horse-battery"'
CONFIG = {"leak_patterns": {"private IP": r"\b192\.168\.\d+\.\d+\b", "owner name": r"\bJane Doe\b"},
          "leak_exempt": {"private IP": ["tests/"]}, "allowed_examples": ["192.168.0.0/16"],
          "public_identity": ["janedoe"]}


@pytest.fixture(autouse=True)
def readable(tmp_path, monkeypatch):
    monkeypatch.setenv("READ_ALLOWED_ROOTS", str(tmp_path))


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    _write(tmp_path, ".repo-release.json", json.dumps(CONFIG))
    _write(tmp_path, "src/app.py", "HOST = '192.168.1.7'\n# 192.168.0.0/16 is the private range\nAUTHOR = 'Jane Doe'\n"
                                    "HANDLE = 'janedoe'\n")
    _write(tmp_path, "src/settings.py", f"TOKEN = '{TOKEN}'\n{PASSWORD_LINE}\n"
                                         f"EXAMPLE = '{TOKEN}'  # secret-scan: allow (a fake for the docs)\n")
    _write(tmp_path, "tests/test_net.py", "BLOCKED = '192.168.1.1'\n")
    return tmp_path


def _hits(out):
    return {(c["id"], c["file"], c["line"]) for c in out["checks"]}


def test_findings_name_file_line_and_pattern_never_the_text(project):
    out = leak_scan(str(project), detail="full")
    assert out["source"] == ".repo-release.json"
    assert _hits(out) == {("private IP", "src/app.py", 1), ("owner name", "src/app.py", 3),
                          ("GitHub token", "src/settings.py", 1), ("password or secret", "src/settings.py", 2)}
    dumped = json.dumps(out)
    for private in ("192.168.1.7", "Jane Doe", TOKEN, "correct-horse-battery"):
        assert private not in dumped


def test_a_public_prefix_is_shown_only_for_kinds_whose_start_is_public(project):
    rows = {c["id"]: c for c in leak_scan(str(project), detail="full")["checks"]}
    assert rows["GitHub token"]["prefix"] == "ghp_" and "prefix" not in rows["password or secret"]


def test_patterns_from_the_call_replace_the_config(project):
    out = leak_scan(str(project), patterns={"owner name": r"\bJane\b"}, secrets=False, detail="full")
    assert out["source"] == "call" and _hits(out) == {("owner name", "src/app.py", 3)}


def test_the_config_file_itself_is_never_scanned(project):
    assert all(c["file"] != ".repo-release.json" for c in leak_scan(str(project), detail="full")["checks"])


def test_venvs_binaries_and_huge_lines_are_skipped(project):
    _write(project, "venv/pyvenv.cfg", "home = x\n")
    _write(project, "venv/lib/site.py", "HOST = '192.168.9.9'\n")
    (project / "blob.bin").write_bytes(b"\0" + b"192.168.3.3" * 10)
    _write(project, "min.js", "var h='192.168.4.4';" + "x" * 3000 + "\n")
    files = {c["file"] for c in leak_scan(str(project), detail="full")["checks"]}
    assert not files & {"venv/lib/site.py", "blob.bin", "min.js"}


def test_no_patterns_and_no_config_still_finds_secrets(tmp_path):
    _write(tmp_path, "a.py", f"T = '{TOKEN}'\n")
    out = leak_scan(str(tmp_path))
    assert out["source"] == "none" and [c["id"] for c in out["checks"]] == ["GitHub token"]


PEM_HEADER = "-----BEGIN " + "PRIVATE KEY-----"


def test_a_multi_line_private_key_is_found(tmp_path):
    """KB-0074: the shared pattern wants the key's body after its header, but this scan reads one line at a time."""
    _write(tmp_path, "id_key", f"{PEM_HEADER}\n{'A' * 64}\n{'B' * 64}\n-----END PRIVATE KEY-----\n")
    assert _hits(leak_scan(str(tmp_path), detail="full")) == {("private key", "id_key", 1)}


def test_a_key_inside_json_is_found(tmp_path):
    _write(tmp_path, "account.json", json.dumps({"private_key": f"{PEM_HEADER}\n{'A' * 64}\n"}) + "\n")
    assert [c["id"] for c in leak_scan(str(tmp_path), detail="full")["checks"]] == ["private key"]


def test_asking_for_no_checks_is_refused_not_clean(tmp_path):
    _write(tmp_path, "a.py", f"T = '{TOKEN}'\n")
    with pytest.raises(LeakScanError, match="nothing to check"):
        leak_scan(str(tmp_path), secrets=False)


def test_a_clean_folder_says_clean(tmp_path):
    _write(tmp_path, "a.py", "x = 1\n")
    (tmp_path / "logo.png").write_bytes(b"\0" * 2_000_000)  # a binary file holds no text to check
    out = leak_scan(str(tmp_path))
    assert out["clean"] is True and out["files"] == 1 and out["not_checked"] == {"binary": 1}


def test_the_default_folder_on_the_server_itself_is_not_clean(monkeypatch):
    """KB-0074's first report: a call with no folder scanned /app and read like a pass for the caller's project."""
    import tools.repo.leak_scan as module
    server_folder = Path(module.__file__).resolve().parents[1]
    monkeypatch.setenv("READ_ALLOWED_ROOTS", str(server_folder))
    monkeypatch.chdir(server_folder)
    monkeypatch.setattr(module, "_files", lambda root, not_checked: iter([("a.py", "x = 1\n")]))
    out = leak_scan()
    assert out["clean"] is False and "own folder" in out["note"]
    assert leak_scan(str(server_folder))["clean"] is True  # naming it on purpose is fine


def test_a_finding_is_not_clean(tmp_path):
    _write(tmp_path, "a.py", f"T = '{TOKEN}'\n")
    assert leak_scan(str(tmp_path))["clean"] is False


def test_an_empty_folder_is_not_clean(tmp_path):
    out = leak_scan(str(tmp_path))
    assert out["files"] == 0 and out["failed"] == 0 and out["clean"] is False


@pytest.mark.parametrize("name, text, key", [
    ("huge.txt", "x = 1\n" * 200_000, "too_big"),
    ("min.js", "x" * 3000 + "\n", "long_lines"),
], ids=["too_big", "long_lines"])
def test_text_it_could_not_check_stops_it_being_clean(tmp_path, name, text, key):
    _write(tmp_path, name, text)
    out = leak_scan(str(tmp_path))
    assert out["failed"] == 0 and out["clean"] is False and out["not_checked"][key] == 1


def test_stopping_at_the_file_limit_is_not_clean(tmp_path, monkeypatch):
    import tools.repo.leak_scan as module
    monkeypatch.setattr(module, "MAX_FILES", 1)
    _write(tmp_path, "a.py", "x = 1\n")
    _write(tmp_path, "b.py", f"T = '{TOKEN}'\n")
    out = leak_scan(str(tmp_path))
    assert out["clean"] is False and out["not_checked"]["over_file_limit"] == 1


@pytest.mark.parametrize("encoding", ["utf-16", "cp1252"])
def test_other_text_encodings_are_still_read(tmp_path, encoding):
    """UTF-16 is what Windows PowerShell's > writes; both were skipped without a word."""
    (tmp_path / "settings.env").write_bytes(f"café = 1\nT = '{TOKEN}'\n".encode(encoding))
    assert [c["id"] for c in leak_scan(str(tmp_path), detail="full")["checks"]] == ["GitHub token"]


@pytest.mark.parametrize("patterns, words", [
    ({f"p{i}": "x" for i in range(51)}, "at most 50"),
    ({"long": "a" * 301}, "characters"),
    ({"runaway": r"(a+)+b"}, "nests quantifiers"),
    ({"runaway": r"(?:\w*)*x"}, "nests quantifiers"),
    ({"broken": "("}, "valid regex"),
])
def test_unsafe_or_broken_patterns_are_refused(tmp_path, patterns, words):
    with pytest.raises(LeakScanError, match=words):
        leak_scan(str(tmp_path), patterns=patterns)


@pytest.mark.parametrize("fine", [r"(abc)+", r"(a|b)+", r"(\+)+", r"\d+\.\d+"])
def test_ordinary_repetition_is_allowed(tmp_path, fine):
    assert leak_scan(str(tmp_path), patterns={"ok": fine})["failed"] == 0


def test_refusals(tmp_path, monkeypatch):
    with pytest.raises(LeakScanError, match="detail"):
        leak_scan(str(tmp_path), detail="everything")
    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.setenv("READ_ALLOWED_ROOTS", str(inside))
    with pytest.raises(LeakScanError, match="outside"):
        leak_scan(str(tmp_path))
