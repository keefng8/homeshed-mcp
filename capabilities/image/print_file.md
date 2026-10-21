# Image — Print file

## ID
`image.print_file`

## Purpose
Turn an accepted design into print-ready files for a print-on-demand product (Printify): a **transparent PNG at the
print area's exact size, 300 DPI, at most N flat colours**, in a **light-tee** and a **dark-tee** version (near-black
ink becomes off-white). The design must be on a plain background (white or one colour): the background that touches
the edges is removed, so white inside the design stays. Runs on the image service's CPU (no GPU, no download).

## Parameters
| Name | Notes |
|---|---|
| `image` | The design's file name (`saved_as` from image.generate / image.status), local or from a paid provider. |
| `width_px`, `height_px` | The print area, 500-8,000 px each. Printify gives it per product and print position. |
| `variants` | `["light", "dark"]` (default both). |
| `colours` | 0 keeps every colour (default; DTG prints full colour); 2-8 reduces to flat colours. |
| `vignette` | `"round"` or `"cloud"` for a full-scene design (no plain background needed): the picture stays solid to 88% of the way out, then a short soft rim of hard-edged halftone dots (the owner, 2026-10-08: the first, wider fade hid most of the image). `"round"` is an oval; `"cloud"` a near-rectangle with gentle bumps, so the corners stay. Dots are at least 6 px on a pitch of at least 12 px (0.6% of the print's shorter side) at 300 DPI; smaller ones are dropped, not shrunk, and there's no partial alpha (DTG underbase haloes it). Empty (default): the plain background is removed. A design that already has a transparent background keeps it. |
| `keep_chest` | `true` for a bust (a pet portrait cut off at the chest): a background-coloured area between the design's left and right edges that opens downward, in the lower middle, is kept (to-do #123: 9 dogs printed White only because their white chests went with the background). Gaps opening upward (between ears) or at the sides stay see-through. Default `false`: scenes have such gaps too (under a motorbike). |

## Returns
`{"files": {"light": {"name", "width", "height", "dpi", "colours"}, "dark": {...}}, "folder": "print-files",
"source", "checks"}`. The files are in the image service's `print-files` folder and download by name.

## Errors
A design whose corners differ (not a plain background), a size outside 500-8,000 px, unknown variants, an unknown
image. A source enlarged more than 1.25x (xAI's 1024 px designs: ~3.6x) is first upscaled 4x with Real-ESRGAN x4plus
(BSD-3; 2026-10-09, a dog's fur came out mushy from a plain resize), loaded for each call and freed after, never
resident (the Director's condition), on the GPU when 1.5 GB is free there, else the CPU; `checks.upscaled` says so.
Isolated opaque specks under 10x10 px (under 1 mm) are dropped, except in vignette mode (`checks.specks_removed`).
Vectorising can come later.
