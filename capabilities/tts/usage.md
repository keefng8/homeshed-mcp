# Text to speech — This month's usage

## ID
`tts.usage`

## Purpose
How much of the month's text-to-speech allowance is used, so the station can plan a book (about 700,000 characters)
around it. Read-only; needs no Google key.

## Returns
`{"month", "characters", "calls", "free_allowance", "free_left", "paid_allowance", "left", "resets",
"paid_characters", "paid_usd_estimate", "clients": {app: {"calls", "characters", "refused"}}, "note"}`

- `month` and `resets` are Google's billing months, US Pacific time (`resets` is the 1st of next month).
- `free_allowance` is 1,000,000; `paid_allowance` is the owner's setting (Settings > Text to speech), 0 by default.
- `left` = free + paid allowance - used. `paid_usd_estimate` is characters past the free million at $30 a million.
- `clients`: each app's pieces and characters this month, and how many were refused at the cap. `owner` is the
  owner's own sessions.

Only this server's calls are counted: other use of the same Google account isn't seen. See `synthesize.md` for the
cap and the facts behind it.
