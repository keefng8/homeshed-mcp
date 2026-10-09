# Image — Carousel (social slides)

## ID
`image.carousel`

## Purpose
Turn designs into social slides for a post: each design centred on a 1080x1350 (4:5: Threads and Instagram feed
posts and carousels) or 1080x1920 (9:16: reels and stories) canvas, on the design's own background colour so each
slide reads as one piece. CPU only, on the image service; slides land in its "promo" folder under the generator's
own name pattern, so `threads.publish(image_names=...)` takes them directly.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `image_names` | yes | 1-10 designs by `saved_as` (a local image, or a paid one kept on the tool server). |
| `shape` | no | `"4:5"` (default) or `"9:16"`. |
| `watermark` | no | Brand text (up to 40 characters): small, bottom-right, about 60% opacity, under the design and never over it. Slides only; originals stay clean; leave empty for shop-listing images. |
| `watermark_style` | no | `"corner"` (default): the mark above. `"tiled"`: the text repeated diagonally (30°) over the whole slide, design included, about 18% opacity, 3-4 rows on a 4:5 slide, one across the design's middle, so a crop can't remove it; for social posts. `"both"`: tiled plus the corner mark. Needs `watermark`. |
| `background` | no | `""` = each design's corner colour (off-white for a transparent design), or `"#rrggbb"`. |

## Returns
`{"slides": [{"name", "width", "height", "bytes"}], "folder": "promo", "size"}`.

## Errors
A bad or missing name, more than 10, a wrong shape, colour or watermark style (or a tiled style with no watermark
text), or the image service unreachable. Nothing is saved
unless every slide was made.
