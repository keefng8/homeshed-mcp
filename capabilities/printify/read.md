# Printify — Read (proxy)

## ID
`printify.shops.list`, `printify.products.list`, `printify.product.get`, `printify.blueprints.list`,
`printify.print_providers.list`, `printify.orders.list`, `printify.order.get` (all read-only)

## Purpose
Let an app (for example one on another machine) use Printify **without holding the token** (2026-10-03): the tool server reads it from
the vault and returns compact answers. Writes (create product, publish, submit order) aren't exposed yet; when they
come they'll be dry-run first and queued for the owner's approval.

## Switch and credentials
- Owner switch: **Control Panel > Settings > Online shops > "Let apps read your Printify account"**
  (`commerce_printify_enabled`, default **off**).
- `PRINTIFY_API_TOKEN`: a personal access token (Printify > My account > Connections), sent as Bearer with a
  User-Agent, as Printify requires.
- `PRINTIFY_SHOP_ID`: the shop the shop-level tools use (`printify.shops.list` shows the ids; not secret).

## Tools
| Tool | Printify endpoint | Inputs |
|---|---|---|
| `printify.shops.list` | `GET /v1/shops.json` | none |
| `printify.products.list` | `GET /v1/shops/{shop_id}/products.json` | `page`, `limit` 1-20 |
| `printify.product.get` | `GET /v1/shops/{shop_id}/products/{id}.json` | `product_id` |
| `printify.blueprints.list` | `GET /v1/catalog/blueprints.json` (filtered and paged here) | `query`, `offset`, `limit` 1-40 |
| `printify.print_providers.list` | `GET /v1/catalog/blueprints/{id}/print_providers.json` | `blueprint_id` |
| `printify.orders.list` | `GET /v1/shops/{shop_id}/orders.json` | `page`, `limit` 1-10, `status` |
| `printify.order.get` | `GET /v1/shops/{shop_id}/orders/{id}.json` | `order_id` |

Money is in the shop's currency units (Printify's cents divided by 100). **Delivery addresses and customer details
are left out.** Printify's limits: 600 requests a minute overall, catalogue endpoints 100 a minute.

## Safety
As etsy.*: https to `api.printify.com` only, no redirects, 15 s timeout, 8 MB cap, validated ids, scrubbed errors.

## Implementation
`mcp-server/tools/printify/_client.py`, `read.py`; shared `tools/commerce/_http.py`. Tests:
`mcp-server/tests/test_commerce_proxy.py`.

## printify.variants.list (2026-10-07)
`printify.variants.list(blueprint_id, print_provider_id, colour="", offset=0, limit=40)`: each variant's `id`,
`colour`, `size` and `print_areas` (`position`, `width_px`, `height_px`): the size `image.print_file` needs and the
ids `printify.product.create` takes. Printify's catalogue has no costs: create a draft, then `printify.product.get`
shows each variant's cost, so margins come from real numbers.

## printify.shipping (2026-10-07)
`printify.shipping(blueprint_id, print_provider_id, country="", variant_id="")`: Printify's catalogue delivery table
(`GET /v1/catalog/blueprints/{b}/print_providers/{p}/shipping.json`). Returns `handling_time {value, unit}` and
`profiles [{variant_ids, countries, first_item, additional_items, currency}]`, prices in units (cost/100) and in the
currency Printify gives, never converted. `country` keeps the groups that deliver there (else Printify's
`REST_OF_THE_WORLD` group); `variant_id` keeps the groups that include it. At most 40 groups. Asked for by the business
machine (its listing margins count the delivery the buyer pays).

## printify.product.get costs (2026-10-07)
`printify.product.get(product_id, variant_ids=[], offset=0, limit=40)`: every enabled variant now carries `cost` (what
Printify charges, in units) next to `price`. Variants are paged (1-100 a page, `next_offset`) instead of cut at 15, and
`variant_ids` returns just the chosen ones. `currency` is passed through only if Printify's product data has the field
(otherwise null): Printify doesn't state the currency of product costs, so check before converting.

## Mockups (2026-10-07)
`printify.product.get` also returns `mockups` (at most 20): `src`, `position`, `camera` (Printify's camera_label),
`variant_ids` (only the asked ones when `variant_ids` is given, else the first 10), `variant_count`, `is_default`,
`is_selected_for_publishing`. `images` stays the count. `printify.mockup.fetch` copies one into the image store.
