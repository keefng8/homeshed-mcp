"""repo.progress / repo.progress_update: an agent's release progress for a dashboard. Test list drafted by the
local model; the waiting-since and client-name cases added from the agreed plan."""
from __future__ import annotations

import pytest

import clients
from tools.repo import progress as prog

GATES = [{"id": "scan", "title": "Private-detail scan", "status": "done", "evidence": "scan of 9afaeaf: 0"},
         {"id": "tests", "title": "Tests in the assembled repo", "status": "done", "evidence": "948 passed"},
         {"id": "coc", "title": "Code of Conduct contact", "status": "waiting", "evidence": ""},
         {"id": "tag", "title": "Owner's go and tag", "status": "todo"}]


REAL_STORE = prog.STORE  # before any test swaps it: the fixture's Path hid a str here once


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(prog, "STORE", tmp_path / "repo_progress.json")


def test_the_real_store_is_a_path_under_data_dir():
    """The live deploy failed with "'str' object has no attribute 'read_text'" while every patched test passed."""
    from pathlib import Path
    assert isinstance(REAL_STORE, Path) and REAL_STORE.name == "repo_progress.json" and REAL_STORE.parent.name == "usage"


def test_an_update_is_stored_and_summarised():
    out = prog.progress_update("homeshed", title="HomeShed public release", steps=GATES,
                               metrics={"readiness": "27 of 29"}, waiting_on_owner=["Code of Conduct contact email"],
                               note="checked 9afaeaf", agent="Github prepper")
    assert (out["percent"], out["done"], out["total"]) == (50, 2, 4)
    row = prog.progress()["projects"][0]
    assert row["title"] == "HomeShed public release" and row["metrics"] == {"readiness": "27 of 29"}
    assert row["agent"] == "Github prepper" and row["waiting_on_owner"][0]["text"] == "Code of Conduct contact email"


def test_waiting_items_keep_the_date_they_were_first_reported(monkeypatch):
    monkeypatch.setattr(prog.time, "time", lambda: 1000.0)
    prog.progress_update("homeshed", steps=GATES, waiting_on_owner=["demo GIF", "OK to tag"])
    monkeypatch.setattr(prog.time, "time", lambda: 5000.0)
    prog.progress_update("homeshed", waiting_on_owner=["OK to tag", "CoC email"])
    since = {w["text"]: w["since"] for w in prog.progress("homeshed")["waiting_on_owner"]}
    assert since == {"OK to tag": 1000.0, "CoC email": 5000.0}  # "demo GIF" is done, so it's gone


def test_a_partial_update_keeps_the_rest_and_history_is_newest_first():
    prog.progress_update("homeshed", title="HomeShed", steps=GATES, metrics={"readiness": "27 of 29"}, note="first")
    prog.progress_update("homeshed", note="second")
    one = prog.progress("homeshed")
    assert one["title"] == "HomeShed" and one["metrics"] == {"readiness": "27 of 29"} and len(one["steps"]) == 4
    assert [h["note"] for h in one["history"]] == ["second", "first"]


def test_project_details_are_kept_linked_and_merged():
    """2026-10-01: the card shows where the project is made, its GitHub repo and link, and what's next."""
    prog.progress_update("homeshed", steps=GATES, repo="alice/my-app", visibility="not public yet",
                         branch="main", version="v0.1.0", licence="Apache-2.0", workspace="my-app",
                         next_step="Record the demo on the final code")
    prog.progress_update("homeshed", version="v0.1.1", note="only the version changed")
    row = prog.progress()["projects"][0]
    assert row["repo_url"] == "https://github.com/alice/my-app" and row["visibility"] == "not public yet"
    assert (row["branch"], row["version"], row["licence"]) == ("main", "v0.1.1", "Apache-2.0")
    assert row["workspace"] == "my-app" and row["next_step"] == "Record the demo on the final code"


@pytest.mark.parametrize("kwargs, words", [
    ({"repo": "not a repo"}, "owner/name"),
    ({"repo": "https://github.com/alice/x"}, "owner/name"),
    ({"branch": "main..evil"}, "branch name"),
    ({"licence": "Apache 2.0 or whatever"}, "SPDX"),
    ({"workspace": "D:/Work/my-app"}, "not a path"),
    ({"next_step": "copy D:\\Work\\my-app\\x over"}, "local path"),
    ({"waiting_on_owner": [{"text": "Press publish", "link": "http://insecure.example"}]}, "https"),
    ({"waiting_on_owner": [{"text": "Press publish", "link": "javascript:alert(1)"}]}, "https"),
    ({"waiting_on_owner": [{"why": "no text"}]}, "text"),
])
def test_bad_details_are_refused(kwargs, words):
    with pytest.raises(prog.ProgressError, match=words):
        prog.progress_update("homeshed", **kwargs)


def test_a_waiting_item_can_say_why_how_and_where(monkeypatch):
    monkeypatch.setattr(prog.time, "time", lambda: 1000.0)
    prog.progress_update("homeshed", waiting_on_owner=[
        {"text": "Delete the demo key", "why": "It was only for the recording", "how": "Console, API keys, Delete",
         "link": "https://console.anthropic.com/settings/keys"}, "OK to tag"])
    monkeypatch.setattr(prog.time, "time", lambda: 5000.0)
    prog.progress_update("homeshed", waiting_on_owner=[{"text": "Delete the demo key", "how": "Console, then Delete"}])
    [item] = prog.progress("homeshed")["waiting_on_owner"]
    assert item == {"text": "Delete the demo key", "how": "Console, then Delete", "since": 1000.0}


def test_history_keeps_the_last_20():
    for i in range(25):
        prog.progress_update("homeshed", note=f"update {i}")
    assert len(prog.progress("homeshed")["history"]) == 20


def test_a_client_token_names_the_agent_and_cannot_be_overridden():
    token = clients.current_client.set("prep-app")
    try:
        prog.progress_update("homeshed", agent="someone else")
    finally:
        clients.current_client.reset(token)
    assert prog.progress("homeshed")["agent"] == "prep-app"


@pytest.mark.parametrize("kwargs, words", [
    ({"project": "Home Shed"}, "short name"),
    ({"project": "homeshed", "steps": [{"id": "x", "status": "finished"}]}, "status"),
    ({"project": "homeshed", "steps": [{"id": str(i), "status": "todo"} for i in range(41)]}, "at most 40"),
    ({"project": "homeshed", "note": "x" * 201}, "longer than 200"),
    ({"project": "homeshed", "note": "key " + "gh" + "p_" + "A" * 36}, "secret"),
    ({"project": "homeshed", "waiting_on_owner": ["copy D:\\Work\\my-app\\x to the server"]}, "local path"),
    ({"project": "homeshed", "metrics": {"where": "/home/alex/build"}}, "local path"),
])
def test_bad_updates_are_refused_and_nothing_is_stored(kwargs, words):
    with pytest.raises(prog.ProgressError, match=words):
        prog.progress_update(**kwargs)
    assert prog.progress()["projects"] == []


def test_an_unknown_project_is_refused_on_read():
    with pytest.raises(prog.ProgressError, match="no progress"):
        prog.progress("nothing-here")
