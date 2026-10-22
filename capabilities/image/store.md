# image.store

Keep an image you already have, such as a buyer's upload, and get back a name the other image tools take. The main
use is `image.generate(provider="google", input_images=[name], prompt="...")`: a new design from the buyer's
reference.

## Input
- `image_b64`: the image, base64 (a `data:image/...;base64,` start is fine). PNG, JPEG or WebP, checked from the
  bytes themselves, not the label. Up to 10 MB and 4096 px a side.

## Returns
`{"name", "mime", "bytes", "width", "height", "expires_at"}`. `name` looks like
`upload-<app>-20261022-051500-1a2b3c4d.png`.

## Privacy
Every image is re-encoded on the way in: EXIF (camera, GPS location, time) and all other metadata are removed, and
the photo is turned upright first so its orientation survives. Delete it with `image.delete` as soon as you're done.

## Limits
- Kept 7 days, then deleted (the sweep runs on every store call).
- At most 500 uploads kept across all apps; a full store refuses until old ones expire.
- Refused: empty or invalid base64, over 10 MB, over 4096 px a side, a decompression bomb (decodes far larger than
  its file), a damaged image, or any other file type.

## Access
`risk: write`, so an app needs `image.store` granted in Control Panel > API access.

## Implementation
`tools/image/store.py` (Pillow). Files sit with the tool server's own images (`IMAGE_OUTPUT_DIR`) under the
`upload-` prefix; `tools/image/refs.py` already resolves those names.

## Machine-readable definition
`store.json`
