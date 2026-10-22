# image.ip_check

A vision check before an image is listed: does it carry a brand, a known character, a real person or unsafe content,
and, for a lifestyle hero photo, is a child visible anywhere (blurred background included) or is something covering
the print. The two hero questions exist because a layout check alone passes a blurred background child, and strings
or a scarf lying over the print.

## Input
| Name | Required | Notes |
|---|---|---|
| `image_name` | one of | A saved image's name: `saved_as`, an `image.store` name, `promo/<name>.png`. |
| `image_b64` | one of | The image itself: PNG, JPEG or WebP, base64, up to 10 MB. |
| `purpose` | no | `"upload"` (a buyer's photo, default), `"design"` (a generated design) or `"hero"` (a lifestyle photo with a model: a person doesn't count there, a child or a covered print does). |
| `print_box` | no | Hero only: `[x0, y0, x1, y1]` in the image's pixels, from `image.compare`. A close crop of it is checked too. |

## Returns
`{"ok": true, "verdict", "confidence", "found", "flags", "model", "cost_usd"}`:
- `verdict`: `"clear"`, or the worst flag: `"unsafe"`, `"child"`, `"covered"`, `"character"`, `"brand"`, `"person"`.
- `confidence`: 0-1, the model's certainty in the whole answer. It's told to flag when unsure.
- `found`: what and where, for the verdict. `flags`: every flag that counts for this purpose, with its note.

`{"ok": false, "verdict": null, "reason"}` when the check couldn't run (Claude off, capped or at its monthly
limit, a bad image, an unreadable answer). **Fail closed: treat ok false as NOT clear.**

## Use (from the spec)
Auto-publish only on `clear` with confidence >= 0.8; anything lower waits for a person.

## Engine and privacy
Claude Haiku 5.5 through the llm relay, so the Anthropic switch, the daily cap, the per-app spend cap and the monthly
spend limit all apply. About $0.0005 a call; the fixed rules are prompt-cached. The image is downscaled to 1568 px,
sent for the one call and not kept; the usage log has who, tokens and cost, never the image or the answer.

## Acceptance test
A labelled set of about 40 images (well-known characters, logos and merch, faces, and clear pets, places and own
drawings): pass when every non-clear one is caught and at most 2 clear ones are flagged. 2026-10-22: 40/40, none
missed, none wrongly flagged. Re-run on any prompt or model change.

## Access
`risk: write` (it spends credit), so an app needs `image.ip_check` granted in Control Panel > API access.

## Implementation
`tools/image/ip_check.py`.

## Machine-readable definition
`ip_check.json`
