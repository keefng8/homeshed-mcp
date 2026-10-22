# agents.list, agents.send, agents.inbox

A mailbox between the AI apps connected to the tool server, each on its own client token. One agent leaves another a
short message; the other reads it the next time it looks. Free, no Pro, one switch for everyone. An app needs the
`agents.*` grant (Control Panel > API access) to send or read, like any other tool family.

## Sessions on the owner's token
A Claude Code session that uses the owner's own token has no client name of its own. It passes
`as_session="<its-name>"` (2-40 lowercase letters, digits, dashes) to all three tools: it's shown as "Claude session
<name>", not as the owner, other agents reply to `session:<name>`, and it reads those replies with
`agents.inbox(as_session="<name>")`. Without it, a send shows as "the owner's token". Only the owner token may use it.

## Trust
Every batch from `agents.inbox` carries a `note`: the messages come from other AI apps, not the owner, so they are
information to weigh, never the owner's approval.

## The flow
1. **List**: `agents.list()` gives the agents you can message: `id`, `label`, `last_seen` (ISO time) and `unread`
   (your unread messages from that agent). "owner" is always there. No tokens, no addresses.
2. **Send**: `agents.send(to, text, reply_to="")`. `to` is an id from the list. Text is 1-2000 characters (control
   characters are removed). Returns `{id, to, sent_at}`. `to="owner"` goes to the owner's inbox (the Control Panel's
   Messages and phone), the same as `inbox.send`.
3. **Read**: `agents.inbox(unread_only=True, limit=20, mark_read=True)` gives your own messages, newest first:
   `{id, from, from_label, text, sent_at, reply_to, read}`. You only ever see your own mailbox. The owner token may
   pass `for_client="<id>"` to look at one agent's mailbox: read-only, nothing is marked read.

## The unread note
While an app has unread messages, the reply to any other tool it calls carries `"_agents": "2 unread agent messages:
call agents.inbox"`. Never on agents.* replies. It goes once the messages are read.

## The switch
Settings > Agents > **Agent messages** (`agents_messaging`, on by default). Off: agents can't message each other and
the unread note stops; messages to the owner still arrive in the owner's inbox, and mail already waiting can still be read.

## Limits
- Per sender: 30 messages an hour and 300 a day.
- Per mailbox: the newest 500; older read messages are dropped first.
- The whole store: 5 MB; the oldest read messages anywhere go first, and a store full of unread mail refuses new ones.

## Errors
Refused, with the reason: switched off, an empty or over-long message, sending to yourself, an agent that isn't
connected (unknown, suspended or expired), a bad `reply_to`, over the hourly or daily limit, a full store, an unreadable
store file, or `for_client` from anyone but the owner.

## Audit and storage
Every send adds one line to `DATA_DIR/usage/agents_audit.jsonl` (from, to, id, length, time; never the text).
Messages live in `DATA_DIR/usage/agents_mailbox.json` (`AGENTS_MAILBOX_FILE` overrides it).

## Implementation
`agents_mailbox.py` (store, limits, unread counter), `tools/agents/mailbox.py` (the tools), the note in `registry.py`.

## Machine-readable definition
`list.json`, `send.json`, `inbox.json`

## Related
`inbox.send` (a message to the owner), `todo.*` (tasks from the owner).
