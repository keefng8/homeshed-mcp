# Image — Saved prompts

## ID
`image.prompts`

## Purpose
The owner's saved image prompts, the same list as on the Control Panel's Images page: list, add, edit or remove one.
To make an image from a saved prompt, pass its `prompt`, `size`, `upscale` and `folder` to `image.generate`.

## Parameters
| Name | Notes |
|---|---|
| `action` | `list` (default), `add`, `edit`, `remove`. |
| `prompt_id` | For `edit` and `remove`. |
| `prompt` | 1-2,000 characters, for `add` and `edit`. |
| `name` | Up to 80 characters; empty uses the start of the prompt. |
| `size` | `512x512`, `768x768`, `1024x1024` (default), `1024x576`, `576x1024`. |
| `upscale` | 4× after generation. |
| `folder` | The images folder its images go into; empty means the main folder. |

## Returns
`list`: `{"items": [{"id", "name", "prompt", "size", "upscale", "folder"}], "most"}`. `add`/`edit`: the saved prompt.
`remove`: `{"deleted": id}`. At most 200 are kept.

## Errors
`ImageError` for an unknown action, a missing id, a refusal from the image service (its reason is passed on), or the
service not answering.
