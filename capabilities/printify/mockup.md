# Printify — Fetch A Mockup

## ID
`printify.mockup.fetch`

## Purpose
Printify renders product mockups (the design on a shirt, a mug...). This copies one into the tool server's image
store and returns its image name, so promo tools can show the real product: `image.carousel` (slides with the brand
watermark) and `threads.publish`. Read-only towards Printify.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `product_id` | yes | The product's id. |
| `src` | yes | One of its mockups' `src`, from `printify.product.get` ("mockups"). |

## Returns
`{"name", "product_id", "bytes", "mime", "width", "new"}`. `name` is `printify-<product_id>-<8 hex>.jpg|png`; the same
mockup fetched again returns the copy already made (`new: false`).

## Safety
Only Printify's image hosts (`https://images.printify.com/...`, which product.get lists since 2026-10-07, or
`https://images-api.printify.com/...`), only a `src` the product itself lists, no redirects followed, at most
15 MB, JPEG or PNG checked from the bytes. A large mockup may be wider than Threads allows (1440 px): put it through
`image.carousel`, which makes 1080 px slides.
