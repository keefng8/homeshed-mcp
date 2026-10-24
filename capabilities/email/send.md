# Email — send, status

## ID
`email.send` (risk `write`), `email.status` (risk `read`)

## Purpose
Send an email from the owner's own address (the owner, 2026-10-23), with **his approval for every send**. R&D's
verdict (`researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md`): the standard library's SMTP client,
port 587 with STARTTLS or 465 with SSL, the default certificate checks.

## How approval works
`email.send` keeps the email as a draft and leaves the owner a message in the Control Panel's **Messages** (and a
phone push when ntfy is set up) showing the recipients, subject and the start of the text. He replies **send** to
send it; any other reply cancels it. Only the owner can reply there, so no app or agent can approve its own email.
A background check every minute sends approved emails. A draft not answered in 3 days expires. `email.status`
shows each one: `waiting`, `sent`, `cancelled`, `expired` or `failed` (with the reason).

## Set up (Control Panel > Settings > Email)
1. **Outgoing mail server**, e.g. `smtp.gmail.com`, and the **port** (587 unless the provider says 465).
2. **Send from**: your address. **Sign-in name** only if it differs.
3. Store the password under Keys and passwords as `SMTP_PASSWORD`. Gmail: turn on 2-step verification, then make an
   app password at myaccount.google.com/apppasswords and use that. Microsoft (Outlook, Hotmail) is retiring password
   sign-in for SMTP; it needs a different sign-in method, not supported yet.

## Limits
Plain text, no attachments; up to 10 recipients; subject one line up to 200 characters; body up to 20,000; 50 sent a
day. Drafts are kept in `usage/email_outbox.json` (env `EMAIL_OUTBOX_FILE`).
