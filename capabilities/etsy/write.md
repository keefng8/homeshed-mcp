# Etsy — Listing details and deactivate

## IDs
`etsy.listing.update`, `etsy.listing.deactivate` (2026-10-08, the shop's director: both behind one reconnect)

## Before the first use
1. Switch on "Let apps ask to edit your Etsy listings' details" and/or "Let apps deactivate your Etsy listings"
   (Settings > Shops and social).
2. Press **Connect Etsy** again once. With a write switch on, it asks Etsy for `listings_w` (listing edits) as well
   as the read scopes; with none on, it stays read-only. The Connect Etsy card says when a reconnect is needed.

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
| `properties` | no | `{"attribute": "value" or ["values"]}`, e.g. `{"Primary color": "Black", "Holiday": "Christmas"}`. |
| `reapply` | no | Set again everything this tool last set on the listing; the other arguments are ignored. Photos aren't re-applied. |
| `images` | no | 1-5 photos to add: `[{"image": ref, "alt_text"?: up to 500 characters, "rank"?: 1-10}]`. |

**Photos** (2026-10-08, the director: every listing needs 5+, and Printify's API has only the front mockup):
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
- **The re-publish rule** (the director): what this tool last set on each listing is kept
  (`usage/etsy_attributes.json`). When the owner approves a `printify.product.update_live` of that listing, the same
  details are queued again straight after, for approval once Printify has finished (a few minutes). Its approval card
  says so up front.
- **On approval**: the listing is checked to be the shop's again, the fields are patched (`updateListing`) and each
  attribute set (`updateListingProperty`). Lists go as one comma-separated value: Etsy keeps only the last of repeated
  keys (etsy/open-api discussion #1086). All idempotent: a failed approval stays waiting with its error and can be
  approved again.
- **Capped**: `ETSY_EDIT_DAILY_MAX` approved edits in 24 hours (default 20).
- **Processing time isn't here.** Etsy moved it to processing profiles set on each inventory offering
  (`readiness_state_id`), and writing the inventory resends every SKU and price, which can unlink Printify's orders.
  Set it in Etsy, or on the delivery profile.

Returns `{"pending": true, "id", "etsy": summary, "expires_in_h", "how"}`, or `{"pending": false}` when the listing is
already as asked.

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
