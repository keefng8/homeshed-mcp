# Image — Garment preview

## ID
`image.preview`

## Purpose
Show a print file on a plain flat tee, so the owner rates exactly what will be printed (2026-10-08). The file is pasted at its true size: 300 DPI against a size-M tee (20" chest), under the collar, with a
faint dashed outline of the whole print area. No person, no model, no download: drawn on the image service's CPU in
about a second, free. The backdrop is a neutral dark grey for light tees and a light grey for dark ones, so the tee
never blends into it (the owner, 2026-10-09).

## Parameters
| Name | Required | Notes |
|---|---|---|
| `image` | yes | A print file's name from `image.print_file` (`files.light.name`, or `files.dark.name` for navy/black). |
| `garment` | no | `"sand"` (default), `"white"`, `"navy"`, `"black"`, or any `"#rrggbb"` (Printify lists each colour's hex). |
| `print_area` | no | `"front"` (default) or `"back"` (a shallower neck, so the print starts higher). |
| `show_area` | no | The dashed print-area outline (default true). |
| `include_image` | no | Also return the JPEG as `image_b64` + `image_mime` (about 150-300 KB), for an app whose page can't reach the image service. |

## Returns
`{"name", "folder": "previews", "ref": "previews/<name>", "width": 1024, "height": 1024, "garment", "garment_rgb",
"print_area", "print_box": [x, y, w, h], "true_size_in": [w, h], "scaled_to_fit", "source"}`. `ref` works wherever a
tool takes an image name as `"folder/name.jpg"`; the image service serves it at `/image/folder-files/previews/<name>`.
`scaled_to_fit` is true only when a print area is wider or taller than the tee's chest allows (shown smaller).

## Errors
Not a print file name, an unknown garment or print area, a print file the image service doesn't have (404).
