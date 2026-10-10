# Printify — Create a product (draft)

## ID
`printify.product.create`

## Purpose
Upload a print file (from `image.print_file`) and make an **unpublished** product in the owner's Printify shop, so
Printify renders its mockups for review. Nothing is published, listed or sold. Off until the owner switches "Let apps
create Printify products" on (Settings > Shops and social), at most `PRINTIFY_DAILY_MAX` a day (default 20), and every one
is in the client audit trail.

**Publishing is not here yet.** Printify only publishes to a store connected to a sales channel (its docs). Since
2026-10-07 the configured shop is connected to Etsy, so publishing can go through Printify; it will be a separate step
the owner approves each time (to-do #59). Until then this tool makes unpublished drafts only.

## Parameters
| Name | Notes |
|---|---|
| `title` | 1-140 characters. |
| `description` | Up to 5,000 characters. |
| `blueprint_id`, `print_provider_id` | From `printify.blueprints.list` / `printify.print_providers.list`. |
| `variants` | `[{"id", "price_pence"}]`, 1-100; price in the shop currency's minor unit (100-100000). |
| `print_file` | The PNG name `image.print_file` returned. |
| `position` | `front` (default) or `back`. |
| `tags` | Up to 13. |

## Returns
`{"product_id", "image_id", "title", "variants", "published": false, "created_today", "daily_max"}`.

## Errors
A switch off, the daily limit, bad arguments, the print file missing, or Printify refusing (its reason passed on,
never the token). The token needs the `uploads.write` and `products.write` scopes.
