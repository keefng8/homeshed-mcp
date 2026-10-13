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
  `PRINTIFY_PUBLISH_DAILY_MAX` publishes in 24 hours (default 20; 5 until 2026-10-09, raised on the owner's OK at the Director's ask, about
  $0.20 each in Etsy listing fees): past that it stays waiting.
- A request older than 24 hours can't be approved. A publish that fails stays waiting with its error.
- Everything goes to the client audit trail (`printify_pending.py`).

## Switches
`commerce_printify_enabled` (read) and `commerce_printify_publish_enabled` ("Let apps ask to publish Printify products
to your shop"), both in Settings > Shops and social, off until the owner switches them on. The drafts switch isn't needed:
asking to publish is a separate permission.

## Paused by a private add-on (2026-10-08)
An owner's private add-on (`private_publish_guard.py`, left out of the public copy) may pause publishing,
e.g. while their own shop app's stop-loss is on: asking to publish is then refused and approving a waiting
publish fails with "it stays waiting". Without one, or when it fails, nothing is paused: the owner's approval
stays the gate.

## One approval: an app's own owner approval (2026-10-09)
The owner, 2026-10-09: "I don't need to double approve ... One is enough". An app the owner names in **Settings > Online
shops > "Apps whose own approval counts"** (`approvals_trusted_clients`: client names from the API page, separated by
commas; empty by default) can pass `owner_approval: {"id", "by", "at"}` to `printify.product.publish`,
`printify.product.update_live` or `etsy.listing.update`. That's the owner's own approval of the request inside the app.
The request is queued and then approved at once through the same path as the approvals bar
(`printify_pending.decide`), so the daily limits and the re-checks still apply. The history note says "approved in
<app> (<id>, <by>)" and the audit line carries `via`. The phone hears only what matters (the owner: "important info to my phone only"): a publish that ran, and anything that couldn't run and now waits in the bar.

It's refused, and the request waits in the approvals bar as usual with `owner_approval: "not used: <why>"`, when:
- the app isn't named in the setting (the owner's own sessions too);
- `id` isn't 3-80 of letters, digits and `_ . : -`;
- `by` is empty, or starts with "auto" and the app isn't named in "Apps whose AUTO mode counts as your approval"
  (`approvals_trusted_auto_clients`; an app named there also counts for owner taps);
- `at` isn't an ISO time within the last hour (5 minutes ahead allowed);
- or the id was used before (kept 30 days, `usage/app_approvals.json`).
A request that passes but can't run (a daily limit, a re-check) waits too, with the reason. The tool server can't see
the app's approval, so this trusts the app's word; the app must send it only when the owner's own login granted that
request.
Etsy details re-queued after an approved live update (`queue_reapply`) carry no approval: they wait in the bar.
AUTO with zero taps (the owner, 2026-10-09: "auto and full auto on means zero taps from me, director and pm is in control"): for an app in that second list, an approval whose `by` is an automatic actor runs the same way. Both lists are empty until the owner fills them in.
Tests: `tests/test_app_approvals.py`.
