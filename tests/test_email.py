"""email.send / email.status: drafts wait for the owner's reply in Messages, "send" sends, anything else cancels,
unanswered drafts expire, bad input is refused, the SMTP client is a fake and never connects."""
from __future__ import annotations

import pytest

import clients
import owner_inbox
import runtime_settings as rs
from tools.email import send as em


class FakeSMTP:
    sent: list = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        assert context is not None

    def login(self, user, password):
        if FakeSMTP.fail_login:
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"no")
        self.user = user

    def send_message(self, m):
        FakeSMTP.sent.append((self.host, self.port, m))


@pytest.fixture
def mail(monkeypatch, tmp_path):
    monkeypatch.setenv("EMAIL_OUTBOX_FILE", str(tmp_path / "outbox.json"))
    monkeypatch.setattr(owner_inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    rs.update({"email_smtp_host": "smtp.example.com", "email_from": "shop@example.com"})
    import vault
    monkeypatch.setattr(vault, "secret", lambda name, default=None: "app-pass-123" if name == "SMTP_PASSWORD" else default)
    FakeSMTP.sent, FakeSMTP.fail_login = [], False
    monkeypatch.setattr(em.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(em.smtplib, "SMTP_SSL", FakeSMTP)
    import tools.notify.send as ns
    monkeypatch.setattr(ns, "send", lambda **kw: {"sent": True})
    return tmp_path


def draft(**kw):
    kw.setdefault("to", ["sam@example.org"])
    kw.setdefault("subject", "Your order")
    kw.setdefault("body", "It shipped today.")
    return em.send(**kw)


def test_nothing_goes_out_until_the_owner_says_send(mail):
    d = draft()
    assert d["status"] == "waiting for the owner"
    msg = owner_inbox.list_open()[0]
    assert msg["need"] == "decision" and "sam@example.org" in msg["text"] and "Reply \"send\"" in msg["text"]
    assert em.process() == [] and FakeSMTP.sent == []
    owner_inbox.reply(d["message_id"], "Send")
    assert em.process() == [d["id"]]
    host, port, m = FakeSMTP.sent[0]
    assert (host, port) == ("smtp.example.com", 587) and m["To"] == "sam@example.org" and m["From"] == "shop@example.com"
    assert m.get_content().strip() == "It shipped today."
    assert em.status(d["id"])["emails"][0]["status"] == "sent"
    assert owner_inbox.list_open() == []  # the message is closed
    assert em.process() == []  # sent once


def test_any_other_reply_cancels(mail):
    d = draft()
    owner_inbox.reply(d["message_id"], "no, wrong customer")
    em.process()
    assert em.status(d["id"])["emails"][0]["status"] == "cancelled" and FakeSMTP.sent == []


def test_unanswered_drafts_expire(mail, monkeypatch):
    d = draft()
    real = em.time.time
    monkeypatch.setattr(em.time, "time", lambda: real() + em.EXPIRE_S + 1)
    em.process()
    assert em.status(d["id"])["emails"][0]["status"] == "expired"


def test_a_refused_sign_in_is_a_failure_with_a_reason(mail):
    FakeSMTP.fail_login = True
    d = draft()
    owner_inbox.reply(d["message_id"], "send")
    em.process()
    row = em.status(d["id"])["emails"][0]
    assert row["status"] == "failed" and "refused the sign-in" in row["error"]


def test_port_465_uses_ssl(mail):
    rs.update({"email_smtp_port": "465"})
    d = draft(sender_name="Kind Shop", reply_to="help@example.com")
    owner_inbox.reply(d["message_id"], "send")
    em.process()
    _, port, m = FakeSMTP.sent[0]
    assert port == 465 and m["Reply-To"] == "help@example.com" and "Kind Shop" in m["From"]


@pytest.mark.parametrize("kw, says", [
    ({"to": "not-an-address"}, "isn't an email address"), ({"to": []}, "to is empty"),
    ({"to": [f"a{i}@example.org" for i in range(11)]}, "at most 10"),
    ({"subject": "Hi\nBcc: x@evil.example"}, "one line"), ({"body": " "}, "body must be"),
    ({"reply_to": "x"}, "isn't an email address"),
])
def test_bad_input(mail, kw, says):
    with pytest.raises(em.EmailError, match=says):
        draft(**kw)


def test_not_set_up(mail):
    rs.update({"email_smtp_host": ""})
    with pytest.raises(em.EmailError, match="isn't set up"):
        draft()


def test_an_app_sees_only_its_own(mail):
    mine = draft()
    tok = clients.current_client.set("shop-bot")
    try:
        theirs = draft()
        assert [r["id"] for r in em.status()["emails"]] == [theirs["id"]]
    finally:
        clients.current_client.reset(tok)
    assert {r["id"] for r in em.status()["emails"]} == {mine["id"], theirs["id"]}
