# Etsy — Listing details, shop texts and deactivate

## IDs
`etsy.listing.update`, `etsy.listing.deactivate` (2026-10-08: both behind one reconnect),
`etsy.shop.update` (2026-10-10)

## Before the first use
1. Switch on "Let apps ask to edit your Etsy listings' details", "Let apps deactivate your Etsy listings" and/or
   "Let apps ask to edit your Etsy shop" (Settings > Shops and social).
2. Press **Connect Etsy** again once. With a listing switch on, it asks Etsy for `listings_w` (listing edits); with the
   shop switch on, `shops_w` (shop edits); with none on, it stays read-only. The Connect Etsy card says when a
   reconnect is needed.

## etsy.shop.update
Sets the shop's own texts and adds shop sections. **A call never changes anything**: it's checked against the shop and
queued in the approvals bar ("Etsy shop texts"), where "Read the full new text" shows every new text in full.

| Name | Notes |
|---|---|
| `title` | The shop title, the heading on the shop's main page: up to 55 characters (Etsy's limit). Can't be empty. |
| `announcement` | The announcement on the shop's homepage. |
| `sale_message` | The message buyers get after a purchase. |
| `digital_sale_message` | The message buyers of a digital item get. |
| `policy_additional` | The shop's additional policies. |
| `sections` | 1-10 section names to add, up to 24 characters each (Etsy's limit); ones the shop has are skipped. |

- Texts: up to 5,000 characters each (this tool's cap); `""` clears one; omitted keeps it. Only what differs from the
  shop (`getShop`) is sent.
- **No About section**: Etsy's `updateShop` has no such field (OpenAPI spec 3.0.0, checked 2026-10-10). Paste it in
  Shop Manager > Settings > About your shop.
- **Sections**: made with `createShopSection`. On approval the shop's sections are read again and one that exists is
  never made twice (a retry is safe). Put a listing in one with `etsy.listing.update` `shop_section_id`.
- One shop request waits at a time. Capped: `ETSY_SHOP_EDIT_DAILY_MAX` approved edits in 24 hours (default 10).
- On approval: `updateShop` (PUT) with the changed texts, then each new section.

Returns `{"pending": true, "id", "etsy_shop": summary, "expires_in_h", "how"}`, or `{"pending": false}` when the shop is
already as asked. When it runs: `{"fields", "sections": [{"title", "shop_section_id", "made": "now" | "earlier"}]}`.

## etsy.listing.update
Sets the details Printify doesn't manage on one of the shop's listings. **A call never changes anything**: it checks
the change and queues it in the approvals bar ("Etsy listing details"); the owner approves each one.

| Name | Required | Notes |
|---|---|---|
| `listing_id` | yes | The listing's number (`etsy.listings.list`). |
| `taxonomy_id` | no | The category (Etsy's seller taxonomy id); 0 keeps it. |
| `materials` | no | The full list: up to 13, letters, digits and spaces, 45 characters each. Omit to keep. |
| `production_partner_ids` | no | The full list of the shop's production partner ids. Omit to keep. |
| `shipping_profile_id` | no | The delivery profile; 0 keeps it. |
| `shop_section_id` | no | The shop section to show it in (`etsy.shop.update` makes sections); 0 keeps it. |
| `properties` | no | `{"attribute": "value" or ["values"]}`, e.g. `{"Primary color": "Black", "Holiday": "Christmas"}`. `null` removes an attribute (`{"Holiday": null}`), and a re-apply never sets it back. |
| `reapply` | no | Set again everything this tool last set on the listing; the other arguments are ignored. Photos aren't re-applied. |
| `images` | no | 1-5 photos to add: `[{"image": ref, "alt_text"?: up to 500 characters, "rank"?: 1-10}]`. A new photo takes its rank and the others move down; after any photo change the listing's photos are re-numbered 1..n (`photos_reranked` in the result). |
| `remove_images` | no | `listing_image_id`s to delete (`etsy.listing.get` lists them). Can't be undone; never the listing's last photo: add the new ones in the same call, they go up first. |
| `images_alt` | no | New alt text for photos already on the listing: `[{"listing_image_id", "alt_text": up to 500}]`. |
| `images_rank` | no | Move photos already on the listing: `[{"listing_image_id", "rank": 1..photo count}]`, up to 10, each id and rank once; the photos not named keep their order in the slots left (the mockup to 1 makes the hero 2). Every id must be the listing's (checked against Etsy when asked and again on approval). **Low risk**: nothing is uploaded or deleted, so it can be the only thing asked and takes the same approval path as `images_alt` (no delete guard holds it). Not with `images` or `remove_images`. The request and the result carry `photo_order` (every photo's final rank); the result's `photos_reranked` lists the ones that moved. |
| `swap` | no | A raw-artwork swap: an automatic (`auto…` / `bot:…`) approval may delete a photo only when one is removed and its watermarked copy added in the same call, the removed one's alt text says "artwork", it's a 2048 or 2400 px square, not at rank 1 or 2, and not a mockup, worn-in hero or info card; at most `ETSY_SWAP_DAILY_MAX` (60) a day. Any guard failing: it waits for the owner's tap. |
| `hero_swap` | no | Replace the AI lifestyle hero with its copy carrying the AI disclosure bar: an automatic approval may delete only when one photo is removed and one added in the same call, the removed one is at rank 1, both alt texts match `ETSY_HERO_ALT` (a regex; default `worn in`) and neither says "artwork", and a photo that isn't an AI scene (the mockup) stays. Same daily maximum as `swap`. |

Both swaps, on approval: the new photo is read back on Etsy and the old one backed up (file and link) before it's
deleted; either failing stops the delete. An owner's own approval is never held by these guards.

**Photos** (2026-10-08: every listing needs 5+, and Printify's API has only the front mockup):
- `image` is one of: `"folder/name.png"` for an image saved in a folder of the image store under its own name (e.g.
  `"listing/krc-staffy-artwork-2400.png"`, served by the image service's `/image/folder-files/`); a generated image's
  name; an image the tool server keeps (`printify-...jpg` from `printify.mockup.fetch`, paid designs); or a Printify
  mockup address (Printify's image hosts only).
- JPEG, PNG or GIF, up to 10 MB each (this tool's cap). The listing may hold at most 10 photos (Etsy's limit),
  counted when asked. The front mockup Printify published is usually photo 1 already: don't queue it again.
- Each photo is fetched and fingerprinted (sha256) when asked, and a small copy is made by the image service. The
  approval card shows those thumbnails (click one for a closer look). On approval each photo is fetched again; if it
  changed, the approval fails ("decline this and ask again"). Uploads use `uploadListingImage` (multipart, with
  `rank` and `alt_text`). Each one uploaded is marked on the request, so a retry after a failure never uploads it
  twice.
- Photos aren't re-applied after a Printify re-publish: `printify.product.update_live` never re-sends photos.

- **Attributes are matched to Etsy's lists.** The names and values are looked up (ignoring case) in the category's own
  attributes (`getPropertiesByTaxonomyId`); a wrong one is refused with the options. Single-value attributes take one
  value, multi-value ones up to Etsy's maximum. Free-text attributes aren't set here.
- **Only what differs is sent**, checked against the listing and its current attributes when asked.
- **Title, description, tags and prices stay with Printify** (`printify.product.update_live`): set on Etsy directly,
  Printify's next publish would undo them.
- **The re-publish rule**: what this tool last set on each listing is kept
  (`usage/etsy_attributes.json`). When the owner approves a `printify.product.update_live` of that listing that
  re-sends its variants (a price change), images or delivery template, the same details are queued again straight
  after, for approval once Printify has finished (a few minutes). Its approval card says so up front. A words-only
  update (title, description, tags) doesn't queue them: on 2026-10-09 five such publishes left every detail intact
  (category, materials, partners, delivery profile, attributes; checked on Etsy).
- **On approval**: the listing is checked to be the shop's again, the fields are patched (`updateListing`) and each
  attribute set (`updateListingProperty`). Lists go as one comma-separated value: Etsy keeps only the last of repeated
  keys (etsy/open-api discussion #1086). All idempotent: a failed approval stays waiting with its error and can be
  approved again.
- **Capped**: `ETSY_EDIT_DAILY_MAX` approved edits in 24 hours (default 50; 20 until 2026-10-09, when publishes went to 20 a day and each new listing
  needs 2 edits).
- **Processing time isn't here.** Etsy moved it to processing profiles set on each inventory offering
  (`readiness_state_id`), and writing the inventory resends every SKU and price, which can unlink Printify's orders.
  Set it in Etsy, or on the delivery profile.

Returns `{"pending": true, "id", "etsy": summary, "expires_in_h", "how"}`, or `{"pending": false}` when the listing is
already as asked. The summary lists the photos queued ("photos: …"); none listed means none were queued.

When it runs (on approval, or at once with an app's approval) the result has `"photos_asked"`, `"photos_landed"` and
`"photo_results"`: `[{"image", "listing_image_id", "uploaded": "now" | "earlier"}]` ("earlier": landed on a previous try
of the same approval, never uploaded twice). `photos_landed == photos_asked` means every photo is on the listing; an
upload that fails raises instead, and the request stays waiting with its error.

## etsy.listing.deactivate
Takes one active listing off sale **at once** (Etsy state `inactive`). It's a brake, not a publish, so there's no
approval step:
- its own switch (`commerce_etsy_deactivate_enabled`), off by default;
- `reason` is required (5-300 characters);
- at most `ETSY_DEACTIVATE_DAILY_MAX` in 24 hours (default 5), kept in `usage/etsy_deactivated.json`;
- audited, and the owner gets a high-priority push with the listing and the reason;
- only the shop's own active listings.

Undo: Etsy > Shop Manager > Listings > Inactive, select it, Activate.

Returns `{"listing_id", "title", "state": "inactive", "reason", "deactivated_today", "daily_max", "undo"}`.

An app the owner trusts can pass `owner_approval` to run it at once: see `printify/publish.md`, "One approval".
