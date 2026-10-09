# Inbox — Replies

## ID
`inbox.replies`

## Purpose
Your messages to the owner (`inbox.send`) and his replies, newest first. An app's token sees only its own messages;
the owner's own sessions share one token, so pass the `sender` name you sent with.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `sender` | no | The name you sent with; empty means every message sent with your token. |
| `limit` | no | 1-50, default 10. |

## Returns
`{"messages": [{"id", "need", "text", "command", "at", "done", "replies": [{"text", "at"}]}], "waiting": int}`.
`waiting` counts messages he hasn't answered or closed yet.
