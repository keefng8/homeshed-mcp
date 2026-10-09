# Commerce — My requests and the owner's decisions

## ID
`commerce.requests.list`

## Purpose
The owner, 2026-10-08: "when i approve from the dash, does the agent get informed?" Until then an app that asked to
publish a Printify product, update a live one or edit an Etsy listing only found out by noticing the shop had
changed, and reported things as still waiting after they'd been approved. This tool lists an app's own requests and
the owner's decisions, so it checks instead of assuming.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `days` | no | How far back the decisions go, 1-7 (default 7). |

## Returns
```
{"waiting": [{"id", "kind", "product_id" | "listing_id", "summary", "asked_at", "expires_at", "last_error"?}],
 "decided": [{"id", "kind", "product_id" | "listing_id", "summary", "asked_at", "decided_at",
              "outcome": "approved" | "declined" | "expired", "note"?}],
 "days"}
```
- `kind`: `publish` (printify.product.publish), `update` (printify.product.update_live) or `etsy`
  (etsy.listing.update, including a re-apply queued after a re-publish).
- `last_error` on a waiting request: the owner approved it but it failed (say Printify was busy), so it stays waiting
  for another approval or a decline.
- `note` on an approved one: the result's note (e.g. "Printify is pushing the change to Etsy now").
- A request not decided within 24 hours is `expired`.

## Who sees what
An app sees only its own requests. The owner (no app token) sees everyone's, each with its `client`.

## How decisions are kept
`printify_pending.py` writes every outcome to `usage/printify_decisions.json` (7 days, once per request). An owner's
private add-on, `private_decision_hooks.py` with `decided(row)`, may also pass each decision on as it happens. That
includes `failed` approvals. It runs in the background and never holds up or fails a decision. The public copy has
none.
