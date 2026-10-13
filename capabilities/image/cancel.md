# Image — Cancel a job

## ID
`image.cancel`

## Purpose
Stop an image job you no longer need (2026-10-08: a training batch was dropped, but its 4 stand-in jobs kept the GPU
busy for half an hour). A waiting local job leaves the line; a running local job is stopped (its sd-cli is killed,
nothing is saved) and the next in line starts.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `job_id` | yes | The 12-character id `image.generate` returned. |

## Who may cancel what
The image service records which app asked for each job (`requested_by`). An app can cancel only its own jobs; the
owner (no app token) can cancel any. Jobs from before 2026-10-08 carry no `requested_by`, so only the owner can cancel
them.

## Returns
`{"ok", "job_id", "status", "was"?, "note"?}`:
- `"cancelled"`: it was waiting or running and is stopped now (`was` says which).
- `"already done"`: it had finished, failed or been cancelled already (`ok` true).
- `"not found"`: no such job, local or paid (`ok` false).
- `"running"`: a paid provider's job (Nano Banana, xAI...) can't be stopped once sent; it finishes and its cost
  stands (`ok` false).
