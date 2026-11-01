# Commerce — A bot approves listing housekeeping

## ID
`commerce.requests.approve_housekeeping`

## Purpose
The owner, 2026-10-31, said "Yes" to the Director approving Etsy cards that only tidy a listing, so the owner stops
getting those taps. A bot the owner names approves one waiting `etsy.listing.update` card when it changes only
housekeeping: delivery profile (`shipping_profile_id`), shop section (`shop_section_id`), production partners
(`production_partner_ids`), `materials`, category (`taxonomy_id`), attributes (`properties`) and photo alt text
(`images_alt`).

Everything else still waits for the owner: prices, a live product's title, description or tags, photos added, removed
or re-ordered, publishing, deleting, Pinterest, shop texts, and any card that mixes housekeeping with one of these.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `request_id` | yes | The waiting request's id, from `commerce.requests.list`. |
| `bot` | yes | The approving bot's name, e.g. `director`. It must be in Settings > Shops and social > **Bots that may approve listing housekeeping** (empty by default). |

## Returns
What the owner's approval returns: `{"id", "approved": true, "etsy": summary, "result"}`. It runs through the same
approval path, so the Etsy daily edit limit and the re-checks still apply.

## The audit trail
The decision is logged as the bot's, never as the owner's tap: `approvals.status` and the decision log show
`decided_by: "bot:<name>"`, and the tool server's audit log has the `via`.

## Errors
The bot isn't named in the setting; the request isn't waiting; it isn't an Etsy details card; it changes something
that isn't housekeeping; or the call comes from an app. Only the owner's own sessions can call this, not an app with
its own key. The bot's name is the calling session's word: the field check is the safeguard.
