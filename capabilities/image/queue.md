# Image — Queue

## ID
`image.queue`

## Purpose
See whether the local image service is busy, the image being made and the ones waiting, in order. The service makes
one image at a time; `image.generate` never blocks, and later requests wait in line (up to 20). Check this before
queuing a batch, and leave room for the owner's own images.

## Parameters
None.

## Returns
`{"busy", "running": job | null, "waiting": [job, ...], "waiting_count", "room"}`. Each job: `job_id, status,
queue_position, progress, prompt, original_prompt, folder, width, height, elapsed_s`. `room` is how many more can
wait.

## Errors
`ImageError` when `IMAGE_BASE_URL` isn't set or the image service doesn't answer.
