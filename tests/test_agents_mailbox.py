"""agents_mailbox + agents.* tools + the unread note on other tools' replies. Every file under tmp_path."""
import json

import pytest

import agents_mailbox as mb
import clients
import registry


@pytest.fixture
def box(tmp_path, monkeypatch):
    import owner_inbox
    import runtime_settings
    from tools.inbox import send as inbox_send
    monkeypatch.setattr(clients, "CLIENTS_FILE", tmp_path / "clients.json")
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "client_audit.jsonl")
    monkeypatch.setattr(clients, "_cache", (None, {"clients": {}}))
    monkeypatch.setattr(runtime_settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(runtime_settings, "_cache", (None, {}))
    monkeypatch.setattr(owner_inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox_send, "_push", lambda sender, need: False)
    monkeypatch.setattr(mb, "STORE_FILE", tmp_path / "agents_mailbox.json")
    monkeypatch.setattr(mb, "AUDIT_FILE", tmp_path / "agents_audit.jsonl")
    mb.reset_cache()
    for name in ("app-a", "app-b"):
        clients.create(name, note=f"The {name} bot")
    yield mb
    mb.reset_cache()


def switch_off():
    import runtime_settings
    runtime_settings.update({"agents_messaging": False})


def test_discovered_by_registry_with_manifests():
    from manifest import load_manifests
    assert {c.name for c in registry.discover() if c.category == "agents"} == {"list", "send", "inbox"}
    ids = {m["id"]: m for m in load_manifests() if m["category"] == "agents"}
    assert set(ids) == {"agents.list", "agents.send", "agents.inbox"} and ids["agents.list"]["risk"] == "read"


def test_send_and_receive_between_two_clients(box):
    sent = box.send("app-a", "app-b", "  build is green\x07, go ahead\n")
    assert set(sent) == {"id", "to", "sent_at"} and sent["to"] == "app-b"
    assert box.unread("app-b") == 1 and box.unread("app-a") == 0
    got = box.inbox("app-b")
    msg = got["messages"][0]
    assert msg["text"] == "build is green, go ahead" and msg["from"] == "app-a" and msg["from_label"] == "The app-a bot"
    assert "not the owner" in got["note"]  # another app's words are never the owner's approval
    assert got["unread"] == 0 and box.inbox("app-b")["messages"] == []  # read now: unread_only hides it
    assert box.inbox("app-b", unread_only=False)["messages"][0]["read"] is True
    reply = box.send("app-b", "app-a", "thanks", reply_to=msg["id"])
    assert box.inbox("app-a")["messages"][0]["reply_to"] == msg["id"] and reply["id"] != msg["id"]


def test_owner_token_session_names_itself_and_gets_replies(box):
    """2026-10-21: Claude sessions share the owner's token; unnamed they showed as "The owner" and replies went to
    the owner's inbox. as_session gives a session its own box and a label that isn't the owner."""
    sent = box.send(None, "app-a", "hello", as_session="my-project")
    msg = box.inbox("app-a")["messages"][0]
    assert msg["from"] == "session:my-project" and "Claude session my-project" in msg["from_label"]
    assert "session:my-project" in {a["id"] for a in box.agents("app-a")["agents"]}
    box.send("app-a", "session:my-project", "got it", reply_to=sent["id"])
    got = box.inbox(None, as_session="my-project")
    assert got["messages"][0]["text"] == "got it" and box.inbox(None, as_session="my-project")["messages"] == []
    assert box.inbox(None)["messages"] == []  # the plain owner token doesn't read a session's mail
    with pytest.raises(mb.MailboxError):
        box.send("app-a", "app-b", "x", as_session="pretend")  # a client can't take a session name
    with pytest.raises(mb.MailboxError):
        box.send(None, "app-a", "x", as_session="Bad Name!")
    assert "token" in box._label("owner", {})  # an unnamed owner-token sender isn't presented as the owner himself


def test_audit_line_has_no_text(box):
    box.send("app-a", "app-b", "secret plans")
    line = json.loads(box.AUDIT_FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert line["client"] == "app-a" and line["to"] == "app-b" and line["length"] == 12
    assert "secret plans" not in box.AUDIT_FILE.read_text(encoding="utf-8")


@pytest.mark.parametrize("to,text,match", [("app-a", "hi", "yourself"), ("nobody", "hi", "no agent called"),
                                           ("app-b", " \x00 ", "empty"), ("app-b", "x" * 2001, "longer")])
def test_bad_sends_refused(box, to, text, match):
    with pytest.raises(box.MailboxError, match=match):
        box.send("app-a", to, text)


def test_suspended_client_is_not_a_recipient(box):
    clients.set_suspended("app-b", True)
    with pytest.raises(box.MailboxError, match="no agent called"):
        box.send("app-a", "app-b", "hi")
    assert [r["id"] for r in box.agents("app-a")["agents"]] == ["owner"]


def test_bad_reply_to_refused(box):
    with pytest.raises(box.MailboxError, match="reply_to"):
        box.send("app-a", "app-b", "hi", reply_to="../x")


def test_rate_limits(box, monkeypatch):
    monkeypatch.setattr(box, "PER_HOUR", 3)
    for _ in range(3):
        box.send("app-a", "app-b", "ping")
    with pytest.raises(box.MailboxError, match="last hour"):
        box.send("app-a", "app-b", "ping")
    box.send("app-b", "app-a", "pong")  # another sender has its own count
    monkeypatch.setattr(box, "PER_HOUR", 100)
    monkeypatch.setattr(box, "PER_DAY", 3)
    with pytest.raises(box.MailboxError, match="today"):
        box.send("app-a", "app-b", "ping")


def test_mailbox_cap_drops_old_read_first(box, monkeypatch):
    monkeypatch.setattr(box, "MAX_BOX", 3)
    first = box.send("app-a", "app-b", "one")["id"]
    box.inbox("app-b")  # "one" is read now
    keep = [box.send("app-a", "app-b", t)["id"] for t in ("two", "three", "four")]
    left = {m["id"] for m in box.inbox("app-b", unread_only=False, mark_read=False)["messages"]}
    assert left == set(keep) and first not in left
    box.send("app-a", "app-b", "five")  # all unread now: the oldest unread goes
    texts = [m["text"] for m in box.inbox("app-b", unread_only=False)["messages"]]
    assert texts == ["five", "four", "three"]


def test_store_cap(box, monkeypatch):
    monkeypatch.setattr(box, "MAX_STORE_BYTES", 900)
    for i in range(3):
        box.send("app-a", "app-b", f"read one {i} " + "x" * 100)
    box.inbox("app-b")
    for i in range(3):
        box.send("app-a", "app-b", f"new {i} " + "y" * 100)  # fits by dropping read mail
    assert box.STORE_FILE.stat().st_size <= 900
    with pytest.raises(box.MailboxError, match="full of unread"):
        for i in range(10):
            box.send("app-a", "app-b", f"more {i} " + "z" * 100)


def test_caller_isolation(box):
    box.send("app-a", "app-b", "for b only")
    assert box.inbox("app-a")["messages"] == []
    with pytest.raises(box.MailboxError, match="only the owner"):
        box.inbox("app-a", for_client="app-b")
    assert box.agents("app-a")["unread"] == 0 and box.agents("app-b")["unread"] == 1


def test_owner_inspects_read_only(box):
    box.send("app-a", "app-b", "hello")
    seen = box.inbox(None, for_client="app-b")
    assert seen["messages"][0]["text"] == "hello" and seen["unread"] == 1
    assert box.unread("app-b") == 1 and box.inbox("app-b")["messages"][0]["read"] is False


def test_owner_recipient_goes_to_inbox(box):
    import owner_inbox
    sent = box.send("app-a", "owner", "please look at the shop")
    item = owner_inbox.list_open()[0]
    assert item["id"] == sent["id"] and item["client"] == "app-a" and item["sender"] == "The app-a bot"
    assert box.unread("owner") == 0


def test_list_shows_agents_without_tokens(box):
    box.send("app-b", "app-a", "hi")
    out = box.agents("app-a")
    assert [r["id"] for r in out["agents"]] == ["owner", "app-b"] and out["unread"] == 1
    assert out["agents"][1] == {"id": "app-b", "label": "The app-b bot", "last_seen": None, "unread": 1}
    assert "hlc_" not in json.dumps(out) and "token" not in json.dumps(out)


def test_switch_off_refuses_agent_sends_but_not_the_owner(box):
    import owner_inbox
    box.send("app-a", "app-b", "before")
    switch_off()
    with pytest.raises(box.MailboxError, match="Settings > Agents > Agent messages"):
        box.send("app-a", "app-b", "after")
    box.send("app-a", "owner", "still reaches the owner")
    assert owner_inbox.list_open()[0]["text"] == "still reaches the owner"
    assert box.inbox("app-b")["messages"][0]["text"] == "before"  # waiting mail stays readable
    assert "switched off" in box.agents("app-a")["note"]


def test_switch_is_on_the_settings_page_and_on_by_default():
    import runtime_settings
    spec = runtime_settings.SCHEMA["agents_messaging"]
    assert spec["default"] is True and spec["label"] == "Agent messages" and spec["type"] == "bool"


# --- the unread note, through the real registry wrapper --------------------------------------------------------------
@pytest.fixture
def other_tool(box, monkeypatch):
    monkeypatch.setattr(registry.usage, "record", lambda *a, **k: None)
    before = list(registry._REGISTRY)

    @registry.tool(name="ping", category="testnote", doc="x.md")
    def ping():
        return {"pong": True}

    yield registry.get_capability("testnote.ping")
    registry._REGISTRY[:] = before


def call_as(client, cap):
    tokens = clients.current_client.set(client), clients.current_tool.set(f"{cap.category}.{cap.name}")
    try:
        return cap.func()
    finally:
        clients.current_tool.reset(tokens[1])
        clients.current_client.reset(tokens[0])


def test_unread_note_on_other_tools_and_cleared_after_reading(box, other_tool):
    assert call_as("app-b", other_tool) == {"pong": True}
    box.send("app-a", "app-b", "one")
    box.send("app-a", "app-b", "two")
    assert call_as("app-b", other_tool)["_agents"] == "2 unread agent messages: call agents.inbox"
    assert "_agents" not in call_as("app-a", other_tool)  # only the receiver hears
    assert "_agents" not in call_as(None, other_tool)     # the owner's sessions use the owner inbox
    inbox_cap = registry.get_capability("agents.inbox")
    assert "_agents" not in call_as("app-b", inbox_cap)   # never on agents.* replies
    assert call_as("app-b", other_tool) == {"pong": True}


def test_unread_note_suppressed_when_switched_off(box, other_tool):
    box.send("app-a", "app-b", "one")
    switch_off()
    assert call_as("app-b", other_tool) == {"pong": True}


def test_unread_counter_never_rereads_the_store(box, other_tool, monkeypatch):
    box.send("app-a", "app-b", "one")
    monkeypatch.setattr(box, "_load", lambda: pytest.fail("the note read the store"))
    assert call_as("app-b", other_tool)["_agents"].startswith("1 unread")


def test_unreadable_store_refuses_and_note_stays_quiet(box, other_tool):
    box.STORE_FILE.write_text("{not json", encoding="utf-8")
    box.reset_cache()
    with pytest.raises(box.MailboxError, match="unreadable"):
        box.send("app-a", "app-b", "hi")
    assert call_as("app-b", other_tool) == {"pong": True}
