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

## Returns
`{"files": {"light": {"name", "width", "height", "dpi", "colours"}, "dark": {...}}, "folder": "print-files",
"source", "checks"}`. The files are in the image service's `print-files` folder and download by name.

## Errors
A design whose corners differ (not a plain background), a size outside 500-8,000 px, unknown variants, an unknown
image. Version 1 resizes on the CPU and snaps back to the palette; vectorising and the GPU upscaler can come later.
