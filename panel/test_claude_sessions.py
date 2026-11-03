"""Past chats read on the panel's own PC, when no helper lists them (an end user, 2026-11-03: "Pick up where you left
off" was always empty). Claude Code's files are faked under tmp_path."""
import json

import httpx
import pytest

import claude_sessions as cs
# _isolated_state_files is autouse there: importing it keeps every state file (the session secret too) in tmp_path here
from test_main import _isolated_state_files, _patch_mcp_client, owner, viewer  # noqa: F401 (fixtures)

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "66666666-7777-8888-9999-000000000000"
SID3 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def chats(monkeypatch, tmp_path):
    home = tmp_path / ".claude"
    proj = home / "projects" / "D--Work-site"
    proj.mkdir(parents=True)
    (home / "sessions").mkdir()
    work = tmp_path / "site"
    work.mkdir()
    pad = json.dumps({"type": "user", "message": "x" * 100})
    lines = [json.dumps({"type": "user", "cwd": str(work), "entrypoint": "cli"}), *[pad] * 60,
             json.dumps({"type": "custom-title", "customTitle": "Shop website"})]
    (proj / f"{SID}.jsonl").write_text("\n".join(lines), encoding="utf-8")
    (proj / f"{SID2}.jsonl").write_text("\n".join([json.dumps({"type": "user", "cwd": str(work), "entrypoint": "sdk-py"}),
                                                   *[pad] * 60]), encoding="utf-8")  # a script's chat: not listed
    monkeypatch.setattr(cs, "CLAUDE_DIR", home)
    monkeypatch.setenv("PANEL_DATA_DIR", str(tmp_path / "panel"))
    cs._cache.clear()
    return work


def test_with_no_helper_the_panel_lists_this_pcs_chats_and_gives_the_resume_command(owner, chats, monkeypatch):  # noqa: F811
    client, main = owner
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "")
    monkeypatch.setitem(main._helper_missing, "sessions", False)
    d = client.get("/api/claude/sessions").json()
    assert [(s["id"], s["name"], s["folder_ok"]) for s in d["sessions"]] == [(SID, "Shop website", True)]
    assert d["resume_mode"] == "command" and d["resume_on"] is True
    r = client.post(f"/api/claude/sessions/{SID}/resume").json()
    assert r["command"].endswith(f"claude --resume {SID}") and str(chats) in r["command"]
    assert client.post(f"/api/claude/sessions/{SID}/hide").json() == {"id": SID, "hidden": True}
    assert client.get("/api/claude/sessions").json()["sessions"] == []
    assert [s["id"] for s in client.get("/api/claude/sessions?hidden=1").json()["sessions"]] == [SID]
    client.post(f"/api/claude/sessions/{SID}/unhide")
    assert len(client.get("/api/claude/sessions").json()["sessions"]) == 1
    assert client.post(f"/api/claude/sessions/{SID2.replace('6', 'a')}/resume").status_code == 404


def test_a_helper_without_sessions_falls_back_to_this_pc(owner, chats, monkeypatch):  # noqa: F811
    client, main = owner
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://gpu")
    monkeypatch.setitem(main._helper_missing, "sessions", False)
    calls = []
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(
        lambda req: calls.append(req.url.path) or httpx.Response(404, json={"detail": "Not Found"})))
    assert client.get("/api/claude/sessions").json()["sessions"][0]["id"] == SID
    assert "command" in client.post(f"/api/claude/sessions/{SID}/resume").json()
    assert calls == ["/sessions"]  # asked once, then read here


def test_no_chats_on_this_pc_says_why_and_a_viewer_sees_none(owner, viewer, monkeypatch, tmp_path):  # noqa: F811
    client, main = owner
    friend, _, _ = viewer
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "")
    monkeypatch.setattr(cs, "CLAUDE_DIR", tmp_path / "nothing")
    d = client.get("/api/claude/sessions").json()
    assert d["not_set_up"] is True and "No Claude Code chats" in d["error"]
    assert friend.get("/api/claude/sessions").status_code == 403


def test_a_folder_that_could_break_the_quotes_gets_no_cd(chats, tmp_path):
    proj = cs.projects_dir() / "odd"
    proj.mkdir()
    pad = json.dumps({"type": "user", "message": "x" * 100})
    bad = 'C:\\x"; rm -rf ~; "'
    (proj / f"{SID3}.jsonl").write_text("\n".join([json.dumps({"type": "user", "cwd": bad}), *[pad] * 60]),
                                        encoding="utf-8")
    r = cs.resume_command(SID3)
    assert r["command"] == f"claude --resume {SID3}" and r["cd_first"] is True


def test_with_no_helper_the_projects_page_reads_this_pc_and_pins_stay_inside(owner, chats, monkeypatch, tmp_path):  # noqa: F811
    import claude_projects as cp
    client, main = owner
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "")
    cp._git_cache.clear()
    (chats / "README.md").write_text("hi", encoding="utf-8")
    [p] = client.get("/api/projects").json()["projects"]
    assert p["folder"] == str(chats) and p["owner"] == "Shop website" and p["sessions_total"] == 2
    assert {"path": "README.md", "dir": False} in p["key_files"]
    r = client.post("/api/projects/pins", json={"folder": str(chats), "path": "README.md", "note": "start here"})
    assert r.json()["pins"][0]["path"] == "README.md"
    assert client.post("/api/projects/pins", json={"folder": str(chats), "path": "../x"}).status_code == 400
    assert client.post("/api/projects/pins", json={"folder": str(tmp_path / "nope"), "path": "a"}).status_code == 404
    assert client.post("/api/projects/pins/remove", json={"folder": str(chats), "path": "README.md"}).json() == {"pins": []}


def test_rtk_numbers_come_from_this_pc_only_when_rtk_is_installed(owner, monkeypatch):  # noqa: F811
    import rtk_stats
    client, main = owner
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "")
    monkeypatch.setattr(rtk_stats, "rtk_bin", lambda: None)
    assert client.get("/api/tokens").json()["rtk"] == {"available": False, "not_set_up": True}
    monkeypatch.setattr(rtk_stats, "rtk_bin", lambda: "rtk")
    monkeypatch.setattr(rtk_stats, "_json", lambda args, timeout: {
        "summary": {"saved_tokens": 1234}, "daily": [], "supported": [{"command": "grep token=abcd1234", "count": 3}]})
    assert client.get("/api/tokens").json()["rtk"] == {"available": True, "saved_tokens": 1234}
    rtk_stats._discover_cache.update(t=0.0, data=None)
    s = client.get("/api/savings").json()
    assert s["daily"]["available"] is True and s["discover"]["missed"][0]["command"] == "grep [redacted]"
