# Image — History

## ID
`image.history`

## Purpose
Which prompt made which image, so you can see what works and reuse it (added 2026-10-06). Every image the local image
service finishes gets a record beside it (`<name>.json`): the prompt it was made with, the original wording when
`image.generate` enhanced it, the model, seed, size, steps and licence. Paid providers' images already keep a `.json`
record with the same job details. This tool lists both together, newest first. Read-only; it never returns image data
(`image.status` does).

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `limit` | integer | `20` | How many images, 1-100. |

## Returns
`{"images": [{"name", "source": "local" | "paid", "at", "prompt"?, "original_prompt"?, "model"?, "seed"?, "width"?,
"height"?, "provider"?, "license"?}], "count", "note"?}`. `at` is a Unix time. Images made before records were kept
have no prompt. When the local image service doesn't answer, only paid images are listed and `note` says so.

## Errors
`ImageError`: `limit` outside 1-100.

## Implementation
`mcp-server/tools/image/history.py`. Tests: `mcp-server/tests/test_image_history.py`.
