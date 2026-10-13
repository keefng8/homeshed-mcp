# Image — Print check

## ID
`image.compare`

## Purpose
Check a scene an image model made from the real mockup (a person wearing the tee, a styled product shot) before it is
used: is the print in it exactly the print on the reference, and does the tee stand out from the background? (A shop
app's ask and the owner's, 2026-10-09: "the print must stay exactly as printed"; "ensure backgrounds do not clash with
the t-shirt", after a white tee blended into cream colour blocks.) Free, on the image service's CPU, about 2-4 s.
Nothing is saved.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `image` | yes | The scene: an image name (`saved_as`), `"folder/name.png"`, or a Printify mockup address. |
| `reference` | yes | What the print must look like: the mockup the scene was made from, the print file, or the design (same forms). |
| `region` | no | `"print"` (default): only the print on the reference counts (a print file's opaque part; on a mockup, the design on the tee). `"all"`: the whole reference. |

## Returns
`{"ok", "print_ok", "found", "score", "structure", "colour", "weakest_cell": {"score", "where"}, "added_text", "tee":
{"tee_colour", "low_contrast_outline", "edge_contrast", "blends"}, "box": {"x", "y", "w", "h"}, "quad", "inliers",
"matches", "points_on_print", "print_on_reference", "note", "ms"}`.
- `ok`: use the scene. The print is as printed **and** the tee stands out. `note` says what failed, in words.
- `print_ok`: the print alone passes. Use the parts' own pass marks, not `score`. `score` (0-1) is for ranking
  only: flat product shots score about 0.95, real lifestyle scenes 0.68-0.83, a recoloured flat shot 0.76.
- `found`: false when the print isn't in the scene. That covers a different design, most mirrored prints, and a
  print hidden or cropped away.
- `box` / `quad`: where the print is in the scene, in the scene's pixels.
- `tee.blends`: more than 20% of the tee's outline has no clear edge against the background (a white tee on cream
  colour blocks: 30-37%; real park and wall scenes: 2-6%).

## How it decides (pass marks set 2026-10-09)
The print is found by matching SIFT points (OpenCV) and fitting a homography. The scene is warped back onto the
reference and compared small, so folds and the scene's light don't count. Each part has its own pass mark:
- structure 0.70+ (grey-level SSIM);
- colour 0.50+ (Lab distance after removing a colour cast);
- weakest of 16 cells 0.45+;
- no added words of 3+ letters (OCR, as in `image.text_check`).

They were set on 9 real scenes (3 designs, all checked by eye as unchanged; every one passes) and 54 local alterations
of them (another design pasted in, hue turned, mirrored, text added, top third painted over): 53 fail. The one miss
was a nearly symmetric design, mirrored, in a flat shot.

## Errors
A bad region, an image that can't be fetched (the reason is given), not an image, no print found on the reference
(give the print file, or `region: "all"`), over 20 MB.
