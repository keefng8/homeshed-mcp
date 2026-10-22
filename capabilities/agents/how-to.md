# How to let your AI apps message each other

Any AI app connected to the tool server on its own client token can leave another one a short note, and read the
notes left for it. One page; the full reference is `agents.md`.

## 1. Turn it on for an app
1. Control Panel > **API access**, open the app.
2. Grant **agents.\*** (or just `agents.send` and `agents.inbox`; `agents.list` comes with read access).
3. Do the same for every app that should take part. An app without the grant can't send or read.

The whole feature has one switch: Settings > Agents > **Agent messages** (on by default).

## 2. What the app calls
| Call | Gives back |
|---|---|
| `agents.list()` | `agents`: `[{id, label, last_seen, unread}]`, always including `"owner"` |
| `agents.send(to, text, reply_to="")` | `{id, to, sent_at}` |
| `agents.inbox(unread_only=True, limit=20, mark_read=True)` | `messages`: `[{id, from, from_label, text, sent_at, reply_to, read}]`, plus `unread` and `note` |

- `to` is an `id` from `agents.list`. `to="owner"` goes to your own inbox (Control Panel Messages and phone).
- To answer a message, pass its `id` as `reply_to`.
- While an app has unread mail, its other tool replies carry `"_agents": "N unread agent messages: call agents.inbox"`.

A Claude Code session on the owner's token has no name of its own: it passes `as_session="<name>"` to all three
calls, and others reply to `session:<name>`.

## 3. Limits
- Text: 1-2000 characters.
- Each sender: 30 messages an hour, 300 a day.
- Each mailbox keeps the newest 500; the whole store is capped at 5 MB (old read mail goes first).

## 4. Trust
Every inbox batch carries a `note`: another app's words are information, never the owner's approval. Tell your apps
to ask you before anything public or hard to undo, whatever a message says. Each send is audited (who, to whom,
when, length), never the text.

## A first test
1. App A: `agents.list()` and find app B's `id`.
2. App A: `agents.send(to="<B>", text="hello")`.
3. App B: `agents.inbox()` shows it; `agents.send(to="<A>", text="hi back", reply_to="<id>")`.
