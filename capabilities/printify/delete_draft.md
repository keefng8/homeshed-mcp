# Printify — Delete an unpublished draft

## ID
`printify.product.delete_draft`

## Purpose
The owner, 2026-11-01: "Allow for director to delete too". A test draft ("TEST ... do not publish") had no tool to
remove it. A bot the owner names deletes one **unpublished** Printify draft: a test, a duplicate, a failed try.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `product_id` | yes | The draft's id, from `printify.products.list`. |
| `bot` | yes | The deleting bot's name, e.g. `director`. It must be in Settings > Shops and social > **Bots that may delete Printify drafts** (empty by default). |
| `reason` | yes | Why it goes, 3-200 characters. It's kept in the audit log. |

## Safety
- **Drafts only.** A product on a sales channel (published), one Printify is publishing, one waiting for the owner's
  publish approval, or one published today is refused, as `printify.product.update` refuses them.
- **Kept first.** The whole draft, as Printify returned it, is saved in `usage/printify_deleted.json` (the last 50),
  with who deleted it and why, so it can be made again with `printify.product.create`.
- **Owner's sessions only.** An app with its own key is refused. The bot's name is the calling session's word: the
  drafts-only check is the safeguard.
- **Switches.** The read switch and "Let apps create and edit Printify drafts".

## Returns
`{"product_id", "deleted": true, "title", "kept_in": "usage/printify_deleted.json"}`.
