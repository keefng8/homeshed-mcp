# Printify — Edit a draft

## ID
`printify.product.update`

## Purpose
Fix an UNPUBLISHED Printify draft before it's published (2026-10-07: every draft needed hand edits in Printify:
delivery and material bullets, tag swaps, prices). It changes only what's given; everything else stays.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `product_id` | yes | The draft's id (`printify.products.list`). |
| `title` | no | 1-140 characters; empty keeps the title. |
| `description` | no | Up to 5,000 characters; empty keeps it. |
| `tags` | no | The new full list: up to 13, each up to 20 characters (Etsy's limits; longer ones are refused, not cut). Omit to keep them. |
| `variants` | no | `[{"id": variant id, "price_pence": new price, "sku": new SKU}]` for the variants to change (either key or both); the others keep theirs. A SKU is 1-32 letters, digits, dots, dashes, slashes or underscores (Etsy's limit is 32). |

SKUs (2026-10-08): Printify lists `sku` on a variant but doesn't promise it can be edited, so the tool checks
Printify's answer and says in `note` when a SKU wasn't kept. A live product's SKUs never change (see
`printify.product.update_live`).

## Safety
- **Drafts only.** Refused when the product is on a sales channel (it has an external listing), Printify has it locked
  mid-publish, a publish of it waits for the owner's approval (decline that first, so what's approved can't change
  underneath), or it was published today.
- **Price floor.** No price under the one that keeps a 30% margin after the variant's Printify cost and Etsy's UK
  fees with VAT (transaction 6.5%, processing 4% + 20p, regulatory 0.48%, listing 16p, VAT 20% on fees; the buyer
  pays delivery and the % fees count on it, estimated at 400p). For an 1,112p tee that's 2,126p. A high delivery
  estimate keeps the floor on the safe side. Settings: `PRINTIFY_MIN_MARGIN`, `PRINTIFY_DELIVERY_ESTIMATE_PENCE`.
- **Audited and undoable.** Each edit is in the audit log and in `usage/printify_edits.json` with the old values.
  At most `PRINTIFY_EDIT_DAILY_MAX` a day (default 50).
- **Switches.** The read switch plus "Let apps create and edit Printify drafts" (`commerce_printify_write_enabled`).

## Returns
`{"product_id", "changed": [fields], "old": {...}, "published": false, "edits_today", "daily_max"}`, plus `note`
when Printify didn't keep a SKU. When the draft is already as asked, `changed` is empty and nothing is sent to
Printify. A published product: `printify.product.update_live`.
