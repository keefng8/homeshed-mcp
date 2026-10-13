# Spend — per app, per day

## ID
`spend.today` (2026-10-10, for the shop app's bots: each bot is its own API client with its own daily cap)

## Purpose
What each app has spent today on **paid** images and language-model calls, against the daily dollar cap the owner set
for it. Read-only.

## The cap (owner only)
Control Panel > **API access** > an app's card > **Spend cap**: dollars a day, or blank for no cap of its own (the
providers' own daily caps on the Image generation and Language models settings still hold for everyone).

When an app reaches its cap:
- its paid image jobs (`image.generate` with a paid provider) and paid model calls (`llm.chat`) are refused with a
  reason that starts **`SPEND_CAP_REACHED`**, so the app can spot it and pause the bot;
- its free and local tools keep working, including the local image model and `local_ai.*`;
- it resets at midnight UK time.

The owner's own use (no client) is counted as `owner` and never capped.

## What counts, and when
| Kind | Counted | Amount |
|---|---|---|
| Paid image | when the job starts (so two jobs can't both slip under the cap); refunded when the provider made nothing (HTTP 400 or 429) | the model's listed price; a model with no listed price counts as $0.20 |
| Paid model call | when the answer comes back | the provider's own cost when it reports one (xAI does), else the price list's estimate |

A model call's price is only known afterwards, so a call is allowed while the app is under its cap and may take it a
little over; the next one is refused. An image is refused if its price would take the app over.

## Returns
`{"date", "currency": "USD", "clients": [{"client", "image", "llm", "total", "cap_usd", "left_usd", "capped"}],
"note"}`. `client` narrows it to one app.

## Storage
`usage/spend_by_client.json`: dollars per app per kind per day, 31 days kept. Nothing about the prompts or answers.
