# Image — Fetch original

## ID
`image.fetch`

## Purpose
Give the caller a finished job's **full-size original**. `image.status(include_image=True)` only returns a <=1024 px
JPEG preview for local jobs and big paid images; this returns the file itself (2026-10-14: an app got only previews
for its interface art). Read-only; available to the owner and to clients granted it (API access page).

## Parameters
| Name | Notes |
|---|---|
| `job_id` | The 12-hex-char id from image.generate. The job must be `done`. |
| `max_mb` | The largest file returned, default 1 MB, up to 25. The bytes come back as base64 text (a third bigger) in the caller's context: ask for more only to save the file. A bigger file is refused with its size and the max_mb to ask for. |

## Returns
`{"job_id", "name", "image_b64", "image_mime", "bytes"}`. Paid-provider jobs (e.g. Google) are returned in their own
saved format, byte for byte; local sd.cpp jobs are fetched from the image service as PNG. The mime comes from the
file's first bytes.

## Errors
Bad job id, unknown job (the GPU service keeps the last 20 in memory; paid jobs also survive via their sidecar), job
not done, file gone, over max_mb (ask again with the max_mb the error gives) or over 25 MB (take it from the image service's folder instead), backend unreachable.
