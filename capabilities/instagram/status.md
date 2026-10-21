# Instagram — Status

## ID
`instagram.status`

## Purpose
Whether Instagram is connected and posting is switched on, the connected account, posts in the last 24 hours and the
daily limit. Check it before `instagram.publish`. Never shows a token.

## Returns
`{"connected", "enabled", "account", "app_ready", "posted_today", "daily_max"}`.
