"""email.send and email.status: an email through the owner's own mail account (SMTP), never sent until the owner
approves it. See ../../capabilities/email/send.md.

R&D's verdict (researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md): the standard library's smtplib,
587 with STARTTLS or 465 with SSL, the default certificate checks, and an approval for every send. The approval is
a message in the owner's Control Panel Messages (owner_inbox): he replies "send" to send it, anything else cancels.
Only the owner can reply there, so no app or agent can approve its own email. A background check (every minute)
sends approved ones; drafts not answered in 3 days expire.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

import clients
from paths import data_path
from registry import tool

FILE_DEFAULT = "usage/email_outbox.json"
MAX_TO = 10
MAX_SUBJECT = 200
MAX_BODY = 20_000
DAILY_MAX = 50
EXPIRE_S = 3 * 86400
TICK_S = 60
TIMEOUT_S = 20
_ADDR = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")
_lock = threading.Lock()
_started = False
log = logging.getLogger(__name__)


class EmailError(ValueError):
    """Safe to show."""


def _file() -> Path:
    return Path(os.environ.get("EMAIL_OUTBOX_FILE") or data_path(FILE_DEFAULT))


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("drafts"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"drafts": {}}


def _save(d: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def _setting(key: str) -> str:
    try:
        import runtime_settings
        return str(runtime_settings.get(key) or "").strip()
    except Exception:  # noqa: BLE001 - an unreadable store: not set up
        return ""


def config() -> dict:
    """The owner's mail settings; the password stays in the vault and is read only when sending."""
    sender = _setting("email_from")
    return {"host": _setting("email_smtp_host"), "port": int(_setting("email_smtp_port") or 587), "from": sender,
            "username": _setting("email_username") or sender}


def _addresses(to) -> list[str]:
    items = [to] if isinstance(to, str) else list(to or [])
    out = []
    for a in items:
        for part in str(a).split(","):
            part = part.strip()
            if part:
                if not _ADDR.match(part) or len(part) > 254:
                    raise EmailError(f"{part[:60]!r} isn't an email address")
                out.append(part)
    if not out:
        raise EmailError("to is empty")
    if len(out) > MAX_TO:
        raise EmailError(f"at most {MAX_TO} recipients")
    return out


def _sent_today(d: dict) -> int:
    day = time.strftime("%Y-%m-%d")
    return sum(1 for x in d["drafts"].values() if x.get("status") == "sent" and str(x.get("sent_at", "")).startswith(day))


@tool(name="send", category="email", doc="email/send.md")
def send(to: list[str] | str, subject: str, body: str, reply_to: str = "", sender_name: str = "") -> dict:
    """Send an email from the owner's own address, after he approves it. The email waits in his Control Panel
    Messages (and a phone push when that's set up); he replies "send" to send it, anything else cancels. Nothing goes
    out without him. Plain text only, no attachments. Set up under Settings > Email (and SMTP_PASSWORD in the vault).

    Args:
        to: one address or a list (up to 10).
        subject: the subject line.
        body: the message, plain text, up to 20,000 characters.
        reply_to: optional address replies should go to.
        sender_name: optional name shown beside the from address.

    Returns:
        {"id", "status": "waiting for the owner", "message_id" (the owner's Messages item)}; email.status shows
        whether it was sent.
    """
    cfg = config()
    if not (cfg["host"] and cfg["from"]):
        raise EmailError("email isn't set up: the owner fills in Settings > Email and stores SMTP_PASSWORD")
    rcpt = _addresses(to)
    subject = str(subject or "").strip()
    if not subject or len(subject) > MAX_SUBJECT or re.search(r"[\r\n]", subject):
        raise EmailError(f"subject must be one line, 1 to {MAX_SUBJECT} characters")
    body = str(body or "")
    if not body.strip() or len(body) > MAX_BODY:
        raise EmailError(f"body must be 1 to {MAX_BODY:,} characters")
    reply_to = _addresses(reply_to)[0] if reply_to else ""
    sender_name = re.sub(r"[\r\n<>\"]", "", str(sender_name or ""))[:80]
    client = clients.current_client.get() or "owner"
    import owner_inbox
    preview = body if len(body) <= 600 else body[:600] + "…"
    did = "e-" + secrets.token_hex(4)
    msg = owner_inbox.send(f"email ({client})", f"Email {did} wants to go out from {cfg['from']}.\nTo: "
                           f"{', '.join(rcpt)}\nSubject: {subject}\n\n{preview}\n\nReply \"send\" to send it; any other "
                           "reply cancels it.", need="decision", client=client)
    with _lock:
        d = _load()
        d["drafts"][did] = {"id": did, "to": rcpt, "subject": subject, "body": body, "reply_to": reply_to,
                            "sender_name": sender_name, "client": client, "inbox_id": msg["id"],
                            "status": "waiting", "created": time.time()}
        _save(d)
    try:
        from tools.notify.send import send as notify
        notify(f"An email to {rcpt[0]}{' +' + str(len(rcpt) - 1) if len(rcpt) > 1 else ''} waits for your OK in "
               "Messages.", title="Email to approve", tags=["email"])
    except Exception:  # noqa: BLE001 - no phone set up: the panel still shows it
        pass
    return {"id": did, "status": "waiting for the owner", "message_id": msg["id"],
            "next": "He approves it in the Control Panel's Messages; email.status shows when it's sent."}


@tool(name="status", category="email", doc="email/send.md")
def status(email_id: str = "") -> dict:
    """Your emails and where each is: waiting, sent, cancelled, expired or failed (with the reason). The owner sees
    every app's; an app sees its own.

    Args:
        email_id: one email's id; empty lists the last 20.
    """
    client = clients.current_client.get()
    rows = [x for x in _load()["drafts"].values() if client is None or x.get("client") == client]
    if email_id:
        rows = [x for x in rows if x["id"] == email_id]
        if not rows:
            raise EmailError(f"no email {email_id!r}")
    rows = sorted(rows, key=lambda x: -x.get("created", 0))[:20]
    keep = ("id", "to", "subject", "status", "sent_at", "error", "client")
    return {"emails": [{k: x.get(k) for k in keep if x.get(k) is not None} for x in rows]}


def _deliver(x: dict) -> None:
    import vault
    cfg = config()
    password = vault.secret("SMTP_PASSWORD") or ""
    if not (cfg["host"] and cfg["from"] and password):
        raise EmailError("email isn't set up (server, from address or SMTP_PASSWORD missing)")
    m = EmailMessage()
    m["From"] = formataddr((x.get("sender_name") or "", cfg["from"]))
    m["To"] = ", ".join(x["to"])
    m["Subject"] = x["subject"]
    m["Message-ID"] = make_msgid(domain=cfg["from"].split("@")[-1])
    if x.get("reply_to"):
        m["Reply-To"] = x["reply_to"]
    m.set_content(x["body"])
    ctx = ssl.create_default_context()
    try:
        if cfg["port"] == 465:
            with smtplib.SMTP_SSL(cfg["host"], 465, context=ctx, timeout=TIMEOUT_S) as s:
                s.login(cfg["username"], password)
                s.send_message(m)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=TIMEOUT_S) as s:
                s.starttls(context=ctx)
                s.login(cfg["username"], password)
                s.send_message(m)
    except smtplib.SMTPAuthenticationError:
        raise EmailError("the mail server refused the sign-in (check the address and the app password)") from None
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"couldn't send ({type(exc).__name__})") from None


def process() -> list[str]:
    """Send what the owner approved, cancel what he declined, expire what he never answered. Returns ids acted on."""
    import owner_inbox
    acted = []
    with _lock:
        d = _load()
        waiting = [x for x in d["drafts"].values() if x.get("status") == "waiting"]
    if not waiting:
        return acted
    items = {i["id"]: i for i in owner_inbox._load()}
    for x in waiting:
        item = items.get(x["inbox_id"])
        replies = (item or {}).get("replies") or []
        answer = str(replies[-1]["text"]).strip().lower().rstrip(".!") if replies else ""
        new, extra = None, {}
        if answer == "send":
            with _lock:
                over = _sent_today(_load()) >= DAILY_MAX
            if over:
                new, extra = "failed", {"error": f"{DAILY_MAX} emails were sent today already; ask again tomorrow"}
            else:
                try:
                    _deliver(x)
                    new, extra = "sent", {"sent_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
                except EmailError as exc:
                    new, extra = "failed", {"error": str(exc)}
        elif answer:
            new = "cancelled"
        elif item is None or item.get("done_at") or time.time() - x["created"] > EXPIRE_S:
            new = "expired" if item is not None and not item.get("done_at") else "cancelled"
        if new:
            with _lock:
                d = _load()
                row = d["drafts"].get(x["id"])
                if row and row.get("status") == "waiting":
                    row.update(status=new, **extra)
                    _save(d)
            try:
                owner_inbox.mark_done(x["inbox_id"])
            except Exception:  # noqa: BLE001 - already gone
                pass
            acted.append(x["id"])
    return acted


def start() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop() -> None:
        while True:
            time.sleep(TICK_S)
            try:
                process()
            except Exception:  # noqa: BLE001 - a bad tick never stops the loop
                log.exception("email outbox tick failed")
    threading.Thread(target=loop, name="email-outbox", daemon=True).start()
