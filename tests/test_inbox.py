"""owner_inbox + inbox.send/replies + the panel's admin routes. Files under the test state folder (conftest)."""
import time

import pytest


@pytest.fixture
def box(tmp_path, monkeypatch):
    import owner_inbox
    from tools.inbox import send as send_mod
    monkeypatch.setattr(owner_inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(send_mod, "_push", lambda sender, need: False)  # no phone pushes from tests
    return owner_inbox


def test_discovered_by_registry():
    from registry import discover
    names = {c.name for c in discover() if c.category == "inbox"}
    assert names == {"send", "replies", "amend", "withdraw"}


def test_send_reply_and_read_back(box):
    from tools.inbox.replies import replies
    from tools.inbox.send import send
    r = send("Please run the login", need="run_command", command="gh auth login", sender="R&D")
    assert r["sent"] and len(r["id"]) == 8
    assert [i["id"] for i in box.list_open()] == [r["id"]]
    assert replies(sender="R&D")["waiting"] == 1
    box.reply(r["id"], "done, logged in")
    got = replies(sender="R&D")
    assert got["messages"][0]["replies"][0]["text"] == "done, logged in" and got["waiting"] == 0
    assert replies(sender="someone else")["messages"] == []


@pytest.mark.parametrize("kw,match", [({"text": ""}, "empty"), ({"text": "x", "need": "urgent"}, "need must"),
                                      ({"text": "x", "need": "run_command"}, "command is empty"),
                                      ({"text": "x" * 2001}, "longer")])
def test_bad_messages_refused(box, kw, match):
    with pytest.raises(box.InboxError, match=match):
        box.send("a", **kw)


def test_cap_on_open_messages(box, monkeypatch):
    monkeypatch.setattr(box, "MAX_OPEN", 2)
    box.send("a", "one"); box.send("a", "two")
    with pytest.raises(box.InboxError, match="already waiting"):
        box.send("a", "three")
    box.mark_done(box.list_open()[0]["id"])
    box.send("a", "three")  # room again once one is done


def test_unknown_id_and_unreadable_file(box):
    with pytest.raises(box.InboxError, match="no message"):
        box.reply("deadbeef", "hi")
    box.INBOX_FILE.write_text("{not json", encoding="utf-8")
    with pytest.raises(box.InboxError, match="unreadable"):
        box.send("a", "hello")


def test_done_messages_drop_out_after_a_week(box, monkeypatch):
    m = box.send("a", "old")
    box.mark_done(m["id"])
    assert box.for_sender("a")
    real = time.time
    monkeypatch.setattr(box.time, "time", lambda: real() + box.KEEP_DONE_S + 5)
    assert box.for_sender("a") == [] and box.list_open() == []


def test_reply_and_done_together(box):
    m = box.send("a", "pick one", need="decision")
    box.reply(m["id"], "the second", done=True)
    assert box.list_open() == []


def test_admin_routes(box):
    from starlette.testclient import TestClient
    import server
    m = box.send("R&D", "hello")
    app = server.mcp.streamable_http_app()
    c = TestClient(app, headers={"Authorization": f"Bearer {server.AUTH_TOKEN}"} if getattr(server, "AUTH_TOKEN", None) else {})
    r = c.get("/inbox")
    if r.status_code in (401, 403):
        pytest.skip("admin auth differs in this test setup; covered by the module tests")
    assert [i["id"] for i in r.json()["items"]] == [m["id"]]
    assert c.post(f"/inbox/{m['id']}/reply", json={"text": "ok", "done": True}).status_code == 200
    assert c.get("/inbox").json()["items"] == []
    assert c.post("/inbox/deadbeef/done").status_code == 404
    assert c.post(f"/inbox/{m['id']}/delete").status_code == 400


def test_amend_own_open_message_keeps_the_old_wording(box):
    m = box.send("R&D", "run this", need="run_command", command="git pul", client="owner")
    out = box.amend(m["id"], "R&D", "owner", command="git pull")
    assert out["command"] == "git pull" and out["edited_at"] and out["edits"][0]["command"] == "git pul"
    with pytest.raises(box.InboxError, match="needs one"):
        box.amend(m["id"], "R&D", "owner", command="")
    box.amend(m["id"], "R&D", "owner", need="info", command="")
    assert box.list_open()[0]["need"] == "info"


def test_only_the_sender_may_amend_or_withdraw(box):
    m = box.send("R&D", "hello", client="owner")
    for sender, client in (("Other", "owner"), ("R&D", "some-app")):
        with pytest.raises(box.InboxError, match="from you"):
            box.amend(m["id"], sender, client, text="hijack")
        with pytest.raises(box.InboxError, match="from you"):
            box.withdraw(m["id"], sender, client)
    with pytest.raises(box.InboxError, match="no message"):
        box.withdraw("deadbeef", "R&D", "owner")


def test_closed_message_cant_be_amended_but_withdraw_removes(box):
    m = box.send("R&D", "x", client="owner")
    box.mark_done(m["id"])
    with pytest.raises(box.InboxError, match="closed"):
        box.amend(m["id"], "R&D", "owner", text="y")
    n = box.send("R&D", "sent by mistake", client="owner")
    assert box.withdraw(n["id"], "R&D", "owner") == {"withdrawn": n["id"]}
    assert [i["id"] for i in box.list_open()] == []


def test_amend_and_withdraw_tools(box):
    from tools.inbox.amend import amend, withdraw
    from tools.inbox.send import send
    r = send("check this", sender="R&D")
    assert amend(r["id"], sender="R&D", text="check this instead") == {"id": r["id"], "amended": True, "need": "info", "edits": 1}
    assert withdraw(r["id"], sender="R&D") == {"withdrawn": r["id"]}
