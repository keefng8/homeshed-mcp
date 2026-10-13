# Printify — Ask to update a live product

## ID
`printify.product.update_live`

## Purpose
Correct a PUBLISHED product's title, description, tags or prices on the owner's Etsy shop without hand edits
(2026-10-08, the shop's project manager and director). A call never changes anything: it checks the change and
queues it for the owner. Only on the owner's approval (the Control Panel's approvals bar) is the product edited in
Printify and re-published, with only the changed parts sent to Etsy.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `product_id` | yes | The published product's id (`printify.products.list`). |
| `title` | no | 1-140 characters; empty keeps the title. |
| `description` | no | Up to 5,000 characters; empty keeps it. |
| `tags` | no | The new full list: up to 13, each up to 20 characters. Omit to keep them. |
| `variants` | no | `[{"id": variant id, "price_pence": new price, "enabled": true/false}]` for the variants to reprice or put on / take off sale (2026-10-09, without touching SKUs; every variant off is refused); the others keep theirs. A switch made in Printify meanwhile makes the request stale. |

## Safety
- **Published products only.** A draft is refused (use `printify.product.update`), and so is one Printify has locked
  mid-publish or one already waiting for the owner (a publish or an update).
- **Never re-publishes a listing that's off on Etsy** (the re-activation guard, the Director's decisions 2026-10-10 item
  9: on 10 Oct an approved update's re-publish switched the Black Staffy tee back on). The Etsy listing's state is read
  when asked, on approval, and again right before the publish; anything but `active` holds it with "listing is off on
  Etsy: the owner turns it on first", for every client. Etsy unreadable also holds it. Turned off in the seconds between
  the edit and the publish: the edit stays in Printify, unpublished.
- **Backstop.** After each re-publish Etsy is read four times over three minutes (Printify publishes in the
  background). If the listing was off at any point and is on at the end, the owner gets a high-priority push ("Etsy
  listing switched back on").
- **No SKU changes.** Printify matches Etsy orders to products by SKU (R&D, 2026-10-08), so a new SKU on a live
  listing can stop its orders arriving. Any `sku` is refused.
- **Price floor.** The same margin floor (20%, hoodies 15%) as `printify.product.update`.
- **Checked again on approval.** If the product's title, description, tags or the prices being changed differ from
  when it was asked (someone edited it meanwhile), the approval fails and the request stays waiting with the reason:
  decline it and ask again. The edit is built from the product as it is then, so nothing else is sent back stale.
- **Selective publish.** Only the changed parts are published (`title`, `description`, `tags`, `variants`); images,
  key features and the delivery template are left as they are on Etsy.
- **Capped and audited.** At most `PRINTIFY_LIVE_UPDATE_DAILY_MAX` approved updates in 24 hours (default 10, counted
  apart from publishes). Each step is in the audit log; the old values are kept in `usage/printify_published.json`.
- **Switches.** The read switch plus "Let apps ask to update live Printify products"
  (`commerce_printify_live_update_enabled`).

## Returns
`{"pending": true, "id", "update": summary, "changed": [fields], "old": {...}, "expires_in_h", "how"}`: the approval
request, not a change on the shop. `{"pending": false, "changed": []}` when the product is already as asked. On
approval the request's result is `{"product_id", "changed", "old", "publishing": true, "note"}`; Printify takes a
minute or two to push it to Etsy.

An app the owner trusts can pass `owner_approval` to run it at once: see `printify/publish.md`, "One approval".
