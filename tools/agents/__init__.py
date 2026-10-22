"""agents.*: the agent-to-agent mailbox (agents_mailbox.py, 2026-10-21)."""

# A standard feature (2026-10-21): every connected app may message the others unless the owner switches it off.
SETTINGS = {
    "agents_messaging": {
        "type": "bool", "default": True, "group": "Agents",
        "label": "Agent messages",
        "help": "Let the AI apps connected to HomeShed send each other short messages (agents.send / agents.inbox). "
                "Off: they can't message each other; messages to you still arrive in your inbox.",
    },
}
