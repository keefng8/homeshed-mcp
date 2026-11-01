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
| `variants` | no | `[{"id": variant id, "price_pence": new price, "sku": new SKU, "enabled": true/false}]` for the variants to change (any of the keys); the others keep theirs. `"enabled": false` takes a size or colour off sale (2026-10-09); every variant off is refused. A SKU is 1-32 letters, digits, dots, dashes, slashes or underscores (Etsy's limit is 32). |

| `print_areas` | no | `[{"position", "print_file", "scale", "x", "y", "angle"}]`: **replaces** every print area on the draft (2026-10-31). See below. Prices, variants and the floor are left as they are. |

### Print areas (where the design goes)
Each item puts one picture on one print position:
- `position`: a print position this blueprint has at its print provider, such as `front` or `back`
  (`printify.variants.list` shows them). Anything else is refused.
- `print_file`: a PNG name from `image.print_file`. The tool uploads it to Printify, the same way
  `printify.product.create` does.
- `scale`: how wide the picture is, as a share of the print area's width: 1 fills it, 0.3 is about a third. 0.05-2.0.
- `x`, `y`: where the picture's centre sits, from 0 to 1. `x` 0 is the left edge of the area and 1 the right;
  `y` 0 is the top and 1 the bottom. 0.5, 0.5 is the middle.
- `angle`: turn the picture, -180 to 180 degrees. Optional, default 0.

Everything is checked before anything is sent. The draft's variants keep the same print areas (its existing
`variant_ids`). The old layout is kept in the edit record, so it can be put back.

Example: a big back print plus a small left-chest print about 3.5 in wide.
```json
{"product_id": "6ac6b2f14f3f51eafe07a5f7",
 "print_areas": [
   {"position": "back",  "print_file": "20261031-101500-111111.png", "scale": 1,   "x": 0.5, "y": 0.45},
   {"position": "front", "print_file": "20261031-101600-222222.png", "scale": 0.3, "x": 0.7, "y": 0.25}]}
```
On a typical tee front area (about 12 in wide), scale 0.3 is roughly 3.5 in, and x 0.7, y 0.25 puts it high on
the wearer's left chest (the right side of the area as you look at it). Areas differ by blueprint, so check the
mockup (`printify.product.get`, then `printify.mockup.fetch`) before publishing.

SKUs (2026-10-08): Printify lists `sku` on a variant but doesn't promise it can be edited, so the tool checks
Printify's answer and says in `note` when a SKU wasn't kept. A live product's SKUs never change (see
`printify.product.update_live`).

## Safety
- **Drafts only.** Refused when the product is on a sales channel (it has an external listing), Printify has it locked
  mid-publish, a publish of it waits for the owner's approval (decline that first, so what's approved can't change
  underneath), or it was published today.
- **Price floor.** No price under the one that keeps a 20% margin (15% on hoodies, blueprint 77; 30% for all until 2026-10-09) after the variant's Printify cost and Etsy's UK
  fees with VAT (transaction 6.5%, processing 4% + 20p, regulatory 0.48%, listing 16p, VAT 20% on fees; the buyer
  pays delivery and the % fees count on it, estimated at 400p). For an 1,112p tee that's 2,126p. A high delivery
  estimate keeps the floor on the safe side. Settings: `PRINTIFY_MIN_MARGIN`, `PRINTIFY_DELIVERY_ESTIMATE_PENCE`.
- **Audited and undoable.** Each edit is in the audit log and in `usage/printify_edits.json` with the old values
  (for `print_areas`, the whole old layout).
  At most `PRINTIFY_EDIT_DAILY_MAX` a day (default 50).
- **Switches.** The read switch plus "Let apps create and edit Printify drafts" (`commerce_printify_write_enabled`).

## Returns
`{"product_id", "changed": [fields], "old": {...}, "published": false, "edits_today", "daily_max"}`, plus `note`
when Printify didn't keep a SKU. When the draft is already as asked, `changed` is empty and nothing is sent to
Printify. A published product: `printify.product.update_live`.
