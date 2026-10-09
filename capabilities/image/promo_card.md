# Image — Promo card

## ID
`image.promo_card`

## Purpose
A branded promo card for a social post, built around the real product mockup (2026-10-07, the Director bot's
promo-card spec). The mockup is the hero, about half the card's height: its plain backdrop margin is trimmed so the
product fills the frame, and the product itself is only scaled, never cropped, retouched or covered. Around it: the brand's logo, the product name as a big headline, a one-line benefit, the real £ price, a
"Shop now" button and the shop link. Two templates for an A/B test:
- `drop`: bold, for younger buyers: dark navy, a glowing gradient frame, retro sunset stripes, confetti, a gradient
  headline, an optional kicker sticker ("New drop").
- `editorial`: premium, for gift buyers: cream, a thin gold double frame with the brand's stars, the mockup as a
  framed print, a serif headline, the brand's tagline band.

With `frame`, a brand frame image (from the images `frames` folder) replaces the drawn background, and the words sit
on a panel so they stay readable. CPU only, on the image service; the card lands in its `promo` folder under the
generator's own name pattern, so `threads.publish(image_names=[name])` takes it directly.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `product_image` | yes | The mockup's saved name: one from `printify.mockup.fetch` (kept on the tool server, sent as bytes) or a local image. |
| `headline` | yes | The product's name, up to 60 characters; upper case in `drop`. Shrinks to fit two balanced lines. |
| `price_gbp` | yes | The real listed price in pounds, more than 0. Shown as e.g. `£24.99`. Never a placeholder. |
| `template` | no | `"drop"` (default) or `"editorial"`. |
| `benefit` | no | One true line, up to 110 characters (one line if it fits, else two balanced lines). |
| `link_text` | no | The shop link as shown, up to 60 characters. |
| `kicker` | no | Up to 20 characters (longer is refused), e.g. `"New drop"`: a sticker in `drop`, small caps above the headline in `editorial`. Only if true. |
| `shape` | no | `"9:16"` (1080x1920, default) or `"4:5"` (1080x1350). |
| `frame` | no | A brand frame as the background: a style name from the brand kit (`neon-gold`, `marble`, `sunset`, `gradient-glass`, `synthwave`, `halloween`, `christmas`, `valentines`; Grok-made 2026-10-07, about $0.02 each) or an image name from the `frames` folder. |
| `cutout` | no | `false` (default, R&D's pick after the first real cards, 2026-10-07): a framed photo tile, the mockup's plain backdrop margin trimmed to the product plus 5% air. `true`: lift the mockup off its plain studio backdrop onto the scene, with a warm light behind it on a dark scene; only backdrop pixels connected to the edge go (a white tee on white can come out ragged, so proper matting would be needed there). A busy backdrop is left alone and the result says `"cutout": false`. |
| `watermark` | no | Brand text, up to 40 characters; empty = none. |
| `watermark_style` | no | `"tiled"` (default: faint and diagonal across the card, drawn under the text panel, words, price and button so they stay crisp; about 7% on a light card, 12% on a dark one), `"corner"` (small, under the mockup) or `"both"`. |

## Honesty rules (enforced)
The supplier's name (Printify) in any text is refused. Claims ("premium cotton", "limited edition", "exclusive") are
the caller's to check against the live listing: the card prints only what it's given.

## Returns
`{"name", "width", "height", "bytes", "folder": "promo", "size", "template", "hero_share", "frame", "cutout"}`.
On 9:16 cards the logo and the link stay out of the top 150 px and bottom 190 px, which Stories and Reels cover
with their own buttons.
`hero_share` is the mockup's height over the card's (the spec's target is 45-55%).

## Errors
A bad name, shape, template or watermark style; an empty headline; a price of 0 or less; text over its limit or
with `<`/`>`; the supplier named; a frame not in the `frames` folder; an unknown brand; or the image service
unreachable.
