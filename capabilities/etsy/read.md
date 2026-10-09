# Etsy — Read (proxy)

## ID
`etsy.shop.get`, `etsy.listings.list`, `etsy.listing.get`, `etsy.orders.list`, `etsy.order.get` (all read-only)

## Purpose
Let an app (for example one on another machine) use the Etsy shop **without ever holding its keys** (2026-10-03). The app
calls these tools with its own client token; the tool server reads the keys from the Control Panel's vault, calls
Etsy, and returns a compact answer. Writes (listing changes, prices, orders) aren't exposed yet; when they come they
will be dry-run first and queued for the owner's approval, as the Proxmox changes are.

## Switch and credentials
- Owner switch: **Control Panel > Settings > Online shops > "Let apps read your Etsy shop"**
  (`commerce_etsy_enabled`, default **off**). Off: every etsy.* call is refused with that message.
- Vault entries (Keys and passwords), by name only:
  | Name | What |
  |---|---|
  | `ETSY_KEYSTRING` | the app's keystring (etsy.com/developers/your-apps) |
  | `ETSY_SHARED_SECRET` | the app's shared secret: since 2026-02-09 every request sends `x-api-key: keystring:secret` |
  | `ETSY_REFRESH_TOKEN` | the shop owner's OAuth 2.0 refresh token (scopes `shops_r listings_r transactions_r`) |
  | `ETSY_SHOP_ID` | the shop's number (not secret) |
- A missing entry gives "Etsy isn't configured: add <NAME> …"; values are never shown. `etsy.shop.get` and
  `etsy.listing.get` need only the app key; listings, orders need the refresh token too.

## Tokens
`POST https://api.etsy.com/v3/public/oauth/token` (`grant_type=refresh_token`, `client_id=<keystring>`) gives a
1-hour access token, cached **in memory only** and refreshed 2 minutes early, or once on a 401. Etsy rotates the
refresh token on each refresh; the new one is written back with `vault.set_credential` (audited, actor
`etsy-token-refresh`). If the vault can't take it (off, or the token lives only in .env), it's kept in memory and
every result carries a `warning` to re-authorise and store it.

## Tools
| Tool | Etsy endpoint | Inputs |
|---|---|---|
| `etsy.shop.get` | `GET /v3/application/shops/{shop_id}` | none |
| `etsy.listings.list` | `GET /v3/application/shops/{shop_id}/listings` (listings_r) | `state` active/inactive/sold_out/draft/expired, `limit` 1-20, `offset` |
| `etsy.listing.get` | `GET /v3/application/listings/{listing_id}` | `listing_id` |
| `etsy.orders.list` | `GET /v3/application/shops/{shop_id}/receipts` (transactions_r) | `limit` 1-20, `offset`, `was_paid`/`was_shipped` any/yes/no |
| `etsy.order.get` | `GET /v3/application/shops/{shop_id}/receipts/{receipt_id}` | `receipt_id` |

Results stay under about 5,000 characters (titles and descriptions trimmed, pages of at most 20). **Buyers' names,
emails, addresses and messages are left out**; an order keeps its country, money, items and tracking.

## Safety
Host-locked: https to `api.etsy.com` only, redirects refused, 15 s timeout (5 s connect), 8 MB cap, paths and ids
validated before any request. Errors carry the HTTP status, a hint and Etsy's short reason with URLs and every key
or token scrubbed. Client tokens reach these only when granted (`etsy.*`).

## Implementation
`mcp-server/tools/etsy/_client.py`, `read.py`; shared `tools/commerce/_http.py`. Tests (offline, httpx.MockTransport):
`mcp-server/tests/test_commerce_proxy.py`.

## Listing checks (2026-10-07)
For a check after publishing a listing:
- `etsy.listing.get(listing_id, full=false)`: `full=true` returns up to 5,000 characters of the description, and
  `description_chars` is the untrimmed length, so the end of a long description (an AI disclosure) can be checked.
  `images` is `{count, ranks}` (asked for with Etsy's `includes=Images`); `production_partner_ids` when Etsy gives them.
- `etsy.shipping_destinations(shipping_profile_id)`: the profile's destinations (country or Etsy region, prices,
  delivery days) and `includes_eu` (an EU-27 country or Etsy's "eu" region), for a UK-only launch check (EU product
  safety, GPSR). It uses the shop's Etsy connection (scope `shops_r`).
