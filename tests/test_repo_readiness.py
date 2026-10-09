"""repo.readiness: a project folder against the Github prepper's checklist plus the Dockerfile and compose checks.
Runs for real on tmp_path folders. Test list drafted by the local model; a complete folder is built from the
checklist itself, so the test keeps up when the checklist grows."""
from __future__ import annotations

import pytest

from tools.repo.readiness import ReadinessError, checklist, readiness


@pytest.fixture(autouse=True)
def readable(tmp_path, monkeypatch):
    monkeypatch.setenv("READ_ALLOWED_ROOTS", str(tmp_path))
    return tmp_path


def _write(root, rel, text="x\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _complete(root):
    """Every checklist file with the text it must contain, plus what triggers each `when`, all passing."""
    for item in checklist()["items"]:
        _write(root, item["path"], "\n".join((item.get("checks") or {}).get("must_contain") or ["ok"]) + "\n")
    _write(root, "LICENSE", "Apache License\nVersion 2.0, January 2004\n")
    _write(root, "pyproject.toml", '[project]\nname = "demo"\nversion = "1.0"\ndependencies = ["httpx"]\n')
    _write(root, "demo/app.py", "import os\nTOKEN = os.environ.get('TOKEN')\n")
    _write(root, "Dockerfile", "FROM python:3.12-slim@sha256:" + "a" * 64 + "\nHEALTHCHECK CMD true\nUSER app\n")
    _write(root, "docker-compose.yml", 'services:\n  app:\n    ports:\n      - "127.0.0.1:8080:8080"\n')


def test_a_complete_folder_scores_full_marks(tmp_path):
    _complete(tmp_path)
    out = readiness(str(tmp_path))
    assert out["failed"] == 0 and all(c["ok"] for c in out["checks"]), out  # a clean result shows what passed
    n = int(out["score"].split(" of ")[1])
    assert out["score"] == f"{n} of {n}" and out["applies"] == ["apache", "deps", "docker", "publishes", "python", "settings"]


def test_an_empty_folder_needs_the_three_required_files_first(tmp_path):
    out = readiness(str(tmp_path))
    required = {c["id"] for c in out["checks"] if c["level"] == "required"}
    assert required == {"README.md", "LICENSE", ".gitignore"} and out["applies"] == []
    unconditional = sum(1 for i in checklist()["items"] if not i.get("when"))
    assert out["score"] == f"0 of {unconditional}" and all(c["detail"] for c in out["checks"])


def test_leftover_working_notes_and_missing_text_are_named(tmp_path):
    _complete(tmp_path)
    _write(tmp_path, "README.md", "# Demo\n<!-- TODO: write the install steps -->\n")
    _write(tmp_path, ".dockerignore", ".env\n")  # the variants (.env.bak-...) would still go into the image
    rows = {c["id"]: c for c in readiness(str(tmp_path))["checks"]}
    assert "still has: <!-- TODO" in rows["README.md"]["detail"]
    assert "missing: .env.*" in rows[".dockerignore"]["detail"] and rows[".dockerignore"]["level"] == "conditional"


def test_conditional_files_are_asked_for_only_when_they_apply(tmp_path):
    _write(tmp_path, "README.md", "# Demo\n")
    ids = {c["id"] for c in readiness(str(tmp_path), detail="full")["checks"]}
    assert not ids & {"NOTICE", ".dockerignore", "pyproject.toml", ".env.example"}
    _write(tmp_path, "Dockerfile", "FROM python:3.12\n")
    ids = {c["id"] for c in readiness(str(tmp_path), detail="full")["checks"]}
    assert {".dockerignore", ".github/workflows/release.yml", "docker-non-root"} <= ids


def test_the_dockerfile_checks(tmp_path):
    _write(tmp_path, "Dockerfile", "FROM node:20 AS build\nRUN make\nFROM python:3.12-slim\nUSER root\n")
    rows = {c["id"]: c for c in readiness(str(tmp_path), detail="full")["checks"]}  # compact stops at 20 gaps
    assert not rows["docker-non-root"]["ok"] and not rows["docker-healthcheck"]["ok"]
    assert "node:20" in rows["docker-base-pinned"]["detail"] and "python:3.12-slim" in rows["docker-base-pinned"]["detail"]
    _write(tmp_path, "Dockerfile", "FROM python:3.12-slim@sha256:" + "b" * 64 + " AS base\nFROM base\nUSER 10001\n"
                                   "HEALTHCHECK CMD true\n")
    full = {c["id"]: c for c in readiness(str(tmp_path), detail="full")["checks"]}
    assert all(full[i]["ok"] for i in ("docker-non-root", "docker-base-pinned", "docker-healthcheck"))  # stage alias


def test_compose_ports_open_to_the_network_are_flagged(tmp_path):
    _write(tmp_path, "docker-compose.yml", 'services:\n  a:\n    ports:\n      - "8080:8080"\n'
                                           '      # - "9000:9000"\n      - "127.0.0.1:5432:5432"\n')
    row = next(c for c in readiness(str(tmp_path))["checks"] if c["id"] == "compose-local")
    assert "8080:8080" in row["detail"] and "9000" not in row["detail"] and "5432" not in row["detail"]


def test_settings_are_detected_in_code_but_not_in_a_virtualenv(tmp_path):
    _write(tmp_path, "venv/pyvenv.cfg", "home = x\n")
    _write(tmp_path, "venv/lib/site.py", "import os\nos.environ['X']\n")
    assert "settings" not in readiness(str(tmp_path))["applies"]
    _write(tmp_path, "app/config.py", "import os\nPORT = os.getenv('PORT')\n")
    assert "settings" in readiness(str(tmp_path))["applies"]


def test_compact_shows_gaps_and_full_shows_everything(tmp_path):
    _write(tmp_path, "README.md", "# Demo\n")
    compact, full = readiness(str(tmp_path)), readiness(str(tmp_path), detail="full")
    assert all(not c["ok"] for c in compact["checks"]) and "README.md" in compact["passed"]
    assert any(c["ok"] for c in full["checks"]) and compact["score"] == full["score"]


def test_refusals(tmp_path, monkeypatch):
    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.setenv("READ_ALLOWED_ROOTS", str(inside))
    with pytest.raises(ReadinessError, match="outside"):
        readiness(str(tmp_path))
    with pytest.raises(ReadinessError, match="detail"):
        readiness(str(inside), detail="everything")


def test_the_shipped_checklist_is_well_formed():
    data = checklist()
    assert data["items"] and set(data["when"]) == {"python", "apache", "docker", "settings", "publishes", "deps"}
    for item in data["items"]:
        assert item["path"] and item["fix"] and item["level"] in ("required", "recommended", "conditional"), item
        assert (item["level"] == "conditional") == bool(item.get("when")), item
        assert not item.get("when") or item["when"] in data["when"], item


def test_ordinary_words_in_a_readme_are_not_working_notes(tmp_path):
    """The prepper's narrower rule: a to-do app's README or a ${{ }} example isn't a leftover note."""
    _complete(tmp_path)
    _write(tmp_path, "README.md", "# A TODO list app\nUse `${{ secrets.TOKEN }}` in your workflow.\n")
    assert readiness(str(tmp_path))["failed"] == 0


def test_platform_flags_are_not_mistaken_for_the_image(tmp_path):
    """The prepper's catch: FROM --platform=... image, with and without a stage name."""
    pinned = "python:3.12-slim@sha256:" + "c" * 64
    _write(tmp_path, "Dockerfile", f"FROM --platform=linux/amd64 {pinned} AS base\nFROM base\nUSER app\n"
                                   "HEALTHCHECK CMD true\n")
    rows = {c["id"]: c for c in readiness(str(tmp_path), detail="full")["checks"]}
    assert rows["docker-base-pinned"]["ok"], rows["docker-base-pinned"]
