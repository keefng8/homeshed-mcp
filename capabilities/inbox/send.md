# Inbox — Send

## ID
`inbox.send`

## Purpose
Leave the owner a message when you need something from him: just telling him something, a command only he can run
(a login, a password prompt, a blocked action), or a decision. It shows on the Control Panel's **Messages** pill,
where he replies or marks it done, and as a phone push when ntfy is set up (at most one a minute). Read his answer
with `inbox.replies`. He may be away: carry on with other work, and don't poll.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `text` | yes | Plain words, up to 2,000 characters. |
| `need` | no | `info` (default), `run_command`, `decision` or `other`. |
| `command` | with `run_command` | The exact command, up to 1,000 characters. Shown to him, never run. |
| `sender` | no | Your session or project name, so he knows who asked. Defaults to your token's name. |

Never put a secret in a message. Check a command before sending it (folder, exact spelling); if you got something
wrong, fix it with `inbox.amend` or take it back with `inbox.withdraw`, not a second message.

## Returns
`{"id", "sent": true, "pushed": bool, "next"}`.

## Errors
Refused when the text is empty or too long, `need` isn't one of the four, `run_command` has no command, or 100
messages are already waiting for him.

Stored in `DATA_DIR/usage/owner_inbox.json` (`OWNER_INBOX_FILE`); done messages drop out after 7 days.
