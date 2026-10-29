# Text to speech — Fetch an MP3

## ID
`tts.fetch`

## Purpose
Give the caller an MP3 `tts.synthesize` made, byte for byte, so it can save chapters and join them into a book.

## Parameters
| Name | Notes |
|---|---|
| `name` | The name `tts.synthesize` returned. An app gets only the files it made; the owner gets any. |
| `max_mb` | The largest file returned, default 4 MB, up to 20. The bytes come back as base64 text (a third bigger), so ask for more only to save the file. A bigger file is refused with the max_mb to ask for. |

## Returns
`{"name", "audio_b64", "audio_mime": "audio/mpeg", "bytes"}`. The front door passes `audio_b64` through as text (it
only turns `image_b64` into pictures), so no `raw` switch is needed.

## Errors
A name that isn't one of ours, another app's file, a file over 7 days old (tidied away), over max_mb.
