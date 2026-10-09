"""inbox.send. See ../../capabilities/inbox/send.md."""
from __future__ import annotations

import time

import owner_inbox
from registry import tool

PUSH_GAP_S = 60  # at most one phone push a minute, however many messages arrive
_last_push = [0.0]


def _push(sender: str, need: str) -> bool:
    """Best effort: a phone push when ntfy is set up. A message is never refused because the push failed."""
    if time.time() - _last_push[0] < PUSH_GAP_S:
        return False
    try:
        from tools.notify.send import send as notify
        label = {"run_command": "needs you to run a command", "decision": "needs a decision"}.get(need, "left you a message")
        notify(f"{sender} {label}. Open the Control Panel's Messages to read and reply.", title="Message from an agent",
               tags=["speech_balloon"])
    except Exception:  # noqa: BLE001 - no ntfy here, or it's down: the panel still shows it
        return False
    _last_push[0] = time.time()
    return True


@tool(name="send", category="inbox", doc="inbox/send.md")
def send(text: str, need: str = "info", command: str = "", sender: str = "") -> dict:
    """Leave the owner a message on the Control Panel (and his phone, when ntfy is set up). He replies or marks it
    done there; read his answer with inbox.replies.

    Args:
        text: what you need to tell or ask him, in plain words.
        need: info (just telling him) | run_command (he must run something: put it in command) | decision | other.
        command: the exact command for him to run (required when need is run_command). Shown, never run.
        sender: your name, e.g. your session or project name, so he knows who asked and you can find the reply.

    Returns:
        {"id", "sent", "pushed"}.
    """
    import clients
    item = owner_inbox.send(sender, text, need, command, client=clients.current_client.get() or "owner")
    return {"id": item["id"], "sent": True, "pushed": _push(item["sender"], need),
            "next": "Check for his answer later with inbox.replies (no need to poll: he may be away)."}
