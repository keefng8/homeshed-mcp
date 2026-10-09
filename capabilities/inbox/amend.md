# Inbox — Amend and withdraw

## IDs
`inbox.amend`, `inbox.withdraw`

## Purpose
Fix or take back your **own** message to the owner (`inbox.send`) instead of sending a second one. The owner,
2026-10-06: an agent told him to run a command, then corrected it in another message; one message, corrected, is
clearer. Only the token and sender name that sent it can change it.

- `inbox.amend(message_id, sender, text?, command?, need?)`: change only the parts you pass. Not for a message he has
  already closed (send a new one). The panel marks it **edited**; the last 5 old wordings are kept.
- `inbox.withdraw(message_id, sender)`: it leaves his list at once.

## Before sending at all
Check a command before you send it: the right folder, the exact spelling, and that it's the one step he must run
himself. Most corrections are avoidable.

## Errors
Unknown id, or not yours; a closed message; a `run_command` message left without a command.
