# Printify — Ask To Publish

## ID
`printify.product.publish`

## Purpose
Put a Printify draft on the owner's shop (Etsy, through Printify's Etsy connection). Publishing is public: buyers see
the listing and Etsy charges its listing fee. So this tool never publishes by itself: it checks the draft and queues
an approval request. The owner approves each one in the Control Panel's approvals bar ("Publish to Etsy"); only then
is it sent to Printify, after checking the draft again.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `product_id` | yes | The draft's id (`printify.product.create` returned it). |

## Returns
`{"pending": true, "id", "publish", "expires_in_h", "how"}`: the approval request. After the owner approves, Printify
takes a minute or two; `etsy.listings.list` then shows the listing and `printify.product.get` its external id.

## Checks
- Refused: switched off; no such product; already published (it has a shop listing); locked (Printify is busy with it);
  no enabled variants; the same product already waiting; 30 already waiting.
- On approval, checked again (it may have been published by hand meanwhile), and at most
  `PRINTIFY_PUBLISH_DAILY_MAX` publishes in 24 hours (default 5): past that it stays waiting.
- A request older than 24 hours can't be approved. A publish that fails stays waiting with its error.
- Everything goes to the client audit trail (`printify_pending.py`).

## Switches
`commerce_printify_enabled` (read) and `commerce_printify_publish_enabled` ("Let apps ask to publish Printify products
to your shop"), both in Settings > Online shops, off until the owner switches them on. The drafts switch isn't needed:
asking to publish is a separate permission.

## Paused by a private add-on (2026-10-08)
An owner's private add-on (`private_publish_guard.py`, left out of the public copy) may pause publishing,
e.g. while their own shop app's stop-loss is on: asking to publish is then refused and approving a waiting
publish fails with "it stays waiting". Without one, or when it fails, nothing is paused: the owner's approval
stays the gate.
