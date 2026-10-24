# Image — Describe

## ID
`image.describe` (risk `read`; paid per call, about the cost of `image.ip_check`)

## Purpose
Short, plainly visible details of a design's artwork, for writing about a design without seeing it (the shop
station's design-facts sheet, 2026-10-24: its post writer can't see the designs and invented details). Only shapes,
colours, parts and positions ("long gold ears", "coral shapes behind the head"). Never a breed or species, a
character, person, place or brand, a fact, a mood or a guess, and nothing about text in the image, the garment or the
mockup's background. Details containing words such as breed, logo or text are dropped as a second guard.

## Parameters
| Name | Default | Description |
|---|---|---|
| `image_name` | `""` | A generated or stored image's name, `folder/name.png`, or a Printify mockup address. |
| `image_b64` | `""` | Or the image itself, base64. Give exactly one. |
| `count` | `3` | 1 to 6 details. |

## Returns
`{ok: true, details: [...], confidence: "high"|"medium"|"low", model, cost_usd}` or `{ok: false, reason}`.

## Engine
Claude Haiku through the llm relay (`llm/relay.md`): the owner's switch, daily caps, monthly limit and usage log apply.
The image is sent for the one call and never kept. One retry when the answer can't be used.
