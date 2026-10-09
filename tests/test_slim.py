"""slim (tools/docs/slim.py): shrink an over-limit instruction file without losing anything. Case list drafted by
local_ai.ask (Qwen3-Coder-30B), written against the real behaviour with a fake local model."""
import re

import pytest

from tools.docs import slim

ANSWERS = {"Build and deploy": "move | how to build and deploy",
           "Project status history": "Memory | decisions made about the layout",
           "Finished migration": "DROP | the migration that's done"}


def text(nl="\n"):
    return nl.join([
        "# Project rules", "Read this first.", "",
        "## Safety rules", "- Never print secrets.", "",
        "## Build and deploy", "Run the build script, then deploy it with the deploy script. " * 30, "",
        "### Deploy details", "```", "## not a heading", "```", "",
        "## Project status history", "On 2026-09-01 we decided to use the new layout for everything. " * 10, "",
        "## Finished migration", "The old migration is done and superseded by the new flow. " * 10, ""])


def fake_ask(prompt):
    return ANSWERS[re.search(r"Section title: (.*)", prompt).group(1)]


@pytest.fixture
def project(tmp_path):
    (tmp_path / "CLAUDE.md").write_bytes(text().encode())
    return tmp_path


def make_plan(project, **kw):
    kw.setdefault("ask", fake_ask)
    return slim.plan(project / "CLAUDE.md", memory=str(project / "mem"), **kw)


def labels_in(plan_path):
    return slim.read_labels(plan_path)


# --- splitting ---------------------------------------------------------------------------------------------------------
def test_sections_split_at_the_top_level_and_join_back_exactly():
    sections = slim.split(text())
    assert [s["title"] for s in sections] == ["Project rules", "Safety rules", "Build and deploy",
                                              "Project status history", "Finished migration"]
    build = sections[2]["text"]
    assert "### Deploy details" in build and "## not a heading" in build  # subsections and fences stay with it
    assert "".join(s["text"] for s in sections) == text()


def test_windows_line_endings_survive_the_split():
    crlf = text("\r\n")
    assert "".join(s["text"] for s in slim.split(crlf)) == crlf


def test_text_before_the_first_heading_is_its_own_section():
    sections = slim.split("Intro line\n\n## A\nbody\n")
    assert sections[0]["id"] == "S00" and sections[0]["text"] == "Intro line\n\n" and sections[1]["title"] == "A"


# --- labels and the plan -----------------------------------------------------------------------------------------------
def test_the_plan_labels_every_section_and_nothing_else_changes(project):
    before = (project / "CLAUDE.md").read_bytes()
    plan_path = make_plan(project)
    assert labels_in(plan_path) == {"S01": "keep", "S02": "keep", "S03": "move", "S04": "memory", "S05": "drop"}
    body = plan_path.read_text(encoding="utf-8")
    assert "how to build and deploy" in body and "characters now, limit 10,000" in body
    assert (project / ".slim" / ".gitignore").read_text() == "*\n"  # never committed by accident
    assert (project / "CLAUDE.md").read_bytes() == before


def test_a_move_that_would_save_little_stays(tmp_path):
    """A review found: sections already cut to a one-line pointer were labelled move, which saved almost nothing."""
    (tmp_path / "CLAUDE.md").write_text("## Deploy\nSee `docs/DEPLOY.md` for the build, the checks and the release. "
                                        "Nothing else lives here now; it moved there on the first pass, along with the "
                                        "packaging notes and the list of commands for the release day.\n",
                                        encoding="utf-8")
    assert slim.TINY <= slim.chars(slim.split((tmp_path / "CLAUDE.md").read_text(encoding="utf-8"))[0]["text"]) < 300
    slim.plan(tmp_path / "CLAUDE.md", ask=lambda p: "move | a pointer to the deploy doc", memory=str(tmp_path / "m"))
    row = slim._load_state(tmp_path / "CLAUDE.md")["sections"][0]
    assert row["label"] == "keep" and "save little" in row["what"]


def test_a_plan_still_over_names_the_biggest_sections_that_stay(project):
    body = make_plan(project, limit=100).read_text(encoding="utf-8")
    line = next(x for x in body.splitlines() if x.startswith("Still over."))
    assert "S01 Project rules" in line and "S02 Safety rules" in line and "Build and deploy" not in line


def test_short_sections_stay_without_asking_the_model(project):
    asked = []
    make_plan(project, ask=lambda p: asked.append(p) or fake_ask(p))
    assert not any("Section title: Safety rules" in p or "Section title: Project rules" in p for p in asked)


def test_a_section_titled_as_rules_stays_without_asking(tmp_path):
    """The first real run sent "security rules" to memory: a rule must stay where every session reads it."""
    (tmp_path / "CLAUDE.md").write_text("## Security rules for the API token\n" + "Keep the token in the vault. " * 20
                                        + "\n\n## Working conventions\n" + "Small commits, tests first. " * 20 + "\n",
                                        encoding="utf-8")
    asked = []
    slim.plan(tmp_path / "CLAUDE.md", ask=lambda p: asked.append(p) or "memory | x", memory=str(tmp_path / "m"))
    rows = slim._load_state(tmp_path / "CLAUDE.md")["sections"]
    assert [(r["label"], r["how"]) for r in rows] == [("keep", "title"), ("keep", "title")] and asked == []


def test_without_the_local_model_labels_are_marked_guesses(project):
    def down(prompt):
        raise OSError("connection refused")
    plan_path = make_plan(project, ask=down)
    body = plan_path.read_text(encoding="utf-8")
    assert "(guess)" in body
    got = labels_in(plan_path)
    assert got["S05"] == "drop" and got["S04"] == "memory" and got["S03"] == "move"  # from the titles' words


def test_the_label_is_read_from_any_answer_shape():
    assert slim.parse_answer("Move | how to deploy") == ("move", "how to deploy")
    assert slim.parse_answer("<think>hmm</think>\nLABEL: memory - the decisions") == ("memory", "")
    assert slim.parse_answer("drop") == ("drop", "")
    assert slim.parse_answer("I am not sure") is None


def test_a_section_already_written_elsewhere_is_a_duplicate(project):
    para = "On 2026-09-01 we decided to use the new layout for everything. " * 10
    (project / "docs").mkdir()
    (project / "docs" / "history.md").write_text("# History\n\n" + para.strip() + "\n", encoding="utf-8")
    state = slim._load_state((make_plan(project), project / "CLAUDE.md")[1])
    row = next(r for r in state["sections"] if r["title"] == "Project status history")
    assert row["label"] == "drop" and row["how"] == "duplicate" and "docs/history.md" in row["what"]


# --- apply --------------------------------------------------------------------------------------------------------------
def test_apply_moves_saves_to_memory_drops_and_keeps(project):
    make_plan(project)
    out = slim.apply(project / "CLAUDE.md", today="2026-09-29")
    new = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Never print secrets" in new and "Read this first" in new                 # keep
    assert "## Build and deploy\nDetails: `.claude/docs/build-and-deploy.md`" in new  # move leaves a pointer
    moved = (project / ".claude" / "docs" / "build-and-deploy.md").read_text(encoding="utf-8")
    assert "Run the build script" in moved and "## not a heading" in moved
    memory = (project / "mem" / "project_project_status_history.md").read_text(encoding="utf-8")
    assert memory.startswith("---\nname: project_project_status_history\n") and "decided to use the new layout" in memory
    index = (project / "mem" / "MEMORY.md").read_text(encoding="utf-8")
    assert "(project_project_status_history.md)" in index and max(map(len, index.splitlines())) <= 200
    assert "Project status history" not in new and "superseded by the new flow" not in new  # memory and drop leave
    assert "superseded by the new flow" in (project / ".slim" / "CLAUDE.backup.md").read_text(encoding="utf-8")
    assert out["after"] < out["before"] and out["dropped"] == ["S05"]


def test_labels_edited_in_the_plan_win(project):
    plan_path = make_plan(project)
    body = plan_path.read_text(encoding="utf-8").replace("| S05 | drop |", "| S05 | keep |")
    plan_path.write_text(body, encoding="utf-8")
    slim.apply(project / "CLAUDE.md")
    assert "superseded by the new flow" in (project / "CLAUDE.md").read_text(encoding="utf-8")


def test_apply_refuses_a_changed_file_or_an_unknown_label(project):
    plan_path = make_plan(project)
    body = plan_path.read_text(encoding="utf-8")
    plan_path.write_text(body.replace("| S05 | drop |", "| S05 | shred |"), encoding="utf-8")
    with pytest.raises(slim.SlimError, match="isn't a label"):
        slim.apply(project / "CLAUDE.md")
    plan_path.write_text(body, encoding="utf-8")
    with open(project / "CLAUDE.md", "a", encoding="utf-8") as f:
        f.write("a new line\n")
    with pytest.raises(slim.SlimError, match="changed since the plan"):
        slim.apply(project / "CLAUDE.md")


def test_apply_never_overwrites_an_existing_file(project):
    docs = project / ".claude" / "docs"
    docs.mkdir(parents=True)
    (docs / "build-and-deploy.md").write_text("someone else's notes\n", encoding="utf-8")
    make_plan(project)
    slim.apply(project / "CLAUDE.md")
    assert (docs / "build-and-deploy.md").read_text(encoding="utf-8") == "someone else's notes\n"
    assert "Details: `.claude/docs/build-and-deploy-2.md`" in (project / "CLAUDE.md").read_text(encoding="utf-8")


def test_apply_is_all_or_nothing(project, monkeypatch):
    make_plan(project)
    before = (project / "CLAUDE.md").read_bytes()
    real, calls = slim._write, []

    def failing(path, content):
        calls.append(path)
        if path.name == "CLAUDE.md" and calls.count(path) == 1:
            raise OSError("disk full")
        real(path, content)

    monkeypatch.setattr(slim, "_write", failing)
    with pytest.raises(OSError):
        slim.apply(project / "CLAUDE.md")
    assert (project / "CLAUDE.md").read_bytes() == before
    assert not (project / ".claude" / "docs" / "build-and-deploy.md").exists()
    assert not (project / "mem" / "MEMORY.md").exists()


def test_windows_line_endings_are_kept(tmp_path):
    (tmp_path / "CLAUDE.md").write_bytes(text("\r\n").encode())
    slim.plan(tmp_path / "CLAUDE.md", ask=fake_ask, memory=str(tmp_path / "mem"))
    slim.apply(tmp_path / "CLAUDE.md")
    new = (tmp_path / "CLAUDE.md").read_bytes()
    assert b"\r\n" in new and b"\n" not in new.replace(b"\r\n", b"")
    assert b"\r\n" in (tmp_path / ".claude" / "docs" / "build-and-deploy.md").read_bytes()


# --- finish ---------------------------------------------------------------------------------------------------------------
def test_finish_deletes_the_working_files_once_under_the_limit(project):
    make_plan(project)
    slim.apply(project / "CLAUDE.md")
    slim.finish(project / "CLAUDE.md")
    assert not (project / ".slim").exists()
    assert (project / ".claude" / "docs" / "build-and-deploy.md").exists()  # the moved text stays, of course


def test_finish_refuses_while_still_over_the_limit_unless_told(project):
    make_plan(project, limit=100)
    slim.apply(project / "CLAUDE.md")
    with pytest.raises(slim.SlimError, match="still over"):
        slim.finish(project / "CLAUDE.md")
    assert (project / ".slim" / "CLAUDE.backup.md").exists()
    slim.finish(project / "CLAUDE.md", anyway=True)
    assert not (project / ".slim").exists()


def test_finish_refuses_a_pointer_to_a_missing_file(project):
    make_plan(project)
    slim.apply(project / "CLAUDE.md")
    (project / ".claude" / "docs" / "build-and-deploy.md").unlink()
    with pytest.raises(slim.SlimError, match="missing"):
        slim.finish(project / "CLAUDE.md")


def test_finish_before_apply_is_refused(project):
    make_plan(project)
    with pytest.raises(slim.SlimError, match="run apply first"):
        slim.finish(project / "CLAUDE.md")


# --- command line --------------------------------------------------------------------------------------------------------
def test_the_command_line_runs_plan_apply_finish(project, capsys):
    path = str(project / "CLAUDE.md")
    assert slim.main(["plan", path, "--no-model", "--memory", str(project / "mem")]) == 0
    assert "sections:" in capsys.readouterr().out
    assert slim.main(["apply", path]) == 0 and "Applied:" in capsys.readouterr().out
    assert slim.main(["finish", path]) == 0 and "working files deleted" in capsys.readouterr().out
    assert slim.main(["apply", path]) == 1 and "no plan" in capsys.readouterr().err
