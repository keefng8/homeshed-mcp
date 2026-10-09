# Notify — Send

## ID
`notify.send`

## Purpose
Send a push notification through your [ntfy](https://ntfy.sh) server, so your AI can reach you on your
phone or desktop: a deploy finished, a health check is failing, a long task is done. Nothing calls it
automatically; a client or another tool has to ask for it.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `message` | string | — | Required, non-empty. The notification body. |
| `topic` | string | `""` | Which ntfy topic to publish to. Empty uses the `ntfy_default_topic` setting, then `NTFY_DEFAULT_TOPIC`, then `"mcp-server"`. Subscribe to that topic in your ntfy app, or you won't see anything. |
| `title` | string | — | Optional notification title. |
| `priority` | string | `"default"` | One of `min`, `low`, `default`, `high`, `urgent`/`max`. |
| `tags` | array of strings | — | Optional ntfy tag/emoji short-codes (e.g. `["warning", "computer"]`). |

## Returns
`{sent: true, topic, id, message}` — `id` is ntfy's own message id, useful for tracing a notification
that didn't arrive. A bundled push returns `{sent: false, held: true, summary_in_s, ...}` or
`{sent: false, dropped: why, ...}`.

## Bundling (2026-10-07)
Only what matters buzzes the phone (nexsift's idea, our own code). Per topic: `high`, `urgent` and `max`
always go at once, so use them for anything time-critical. Otherwise the same push (topic, title,
message) again within 30 minutes is dropped, and after 3 pushes in 10 minutes the rest wait and go as
one summary push ("N more updates", up to 10 lines) when the 10 minutes are up. A failed send isn't
counted, so a retry isn't dropped. Kept in memory: a tool-server restart drops a waiting summary.

## Errors
Raises `NotifyError` for: empty `message`, an invalid `priority`, `NTFY_BASE_URL` or `NTFY_AUTH_TOKEN`
not configured, the ntfy server unreachable, or a non-200 response (including 401/403 when the token is
wrong). Never fails silently: sending is the point of the call.

## Configuration
| Var | Purpose |
|---|---|
| `NTFY_BASE_URL` | Your ntfy server, e.g. `https://ntfy.sh` or `http://localhost:8080`. No default. |
| `NTFY_AUTH_TOKEN` | Required. An ntfy access token, sent as `Authorization: Bearer`. |
| `NTFY_DEFAULT_TOPIC` | Optional. The topic used when a call names none. |

## Security
A write tool with a real side effect (a notification lands on your device), but not a destructive one,
so there's no `dry_run`. On a self-hosted ntfy, restrict default access (`auth-default-access`) so a
topic can't be read or written by anyone who guesses its name, and use a token for every call. Don't put
secrets in `message`.

## Implementation
`tools/notify/send.py`. Tests: `tests/test_notify_send.py` (mocked HTTP).

## Machine-readable definition
`send.json`, same directory.
