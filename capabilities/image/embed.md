# image.embed

## ID
`image.embed` (read)

## Purpose
A shop's shadow design judge (2026-10-09, the Director's ask): turn a design image into numbers a small classifier can
learn from, fitted on the owner's rated designs (logistic regression with calibration and cross-validation on the
caller's side). The judge only logs its verdict next to the owner's; nothing acts on it.

## Model
`google/siglip2-base-patch16-224`, Apache-2.0 (commercial use allowed), free. Only its vision half (~93M parameters)
is loaded. The vector is the pooled image embedding, L2-normalised, 768 numbers. Vectors from different models aren't
comparable, so callers store the model name with each one and refit when it changes.

## Where it runs (the Director's condition: never take VRAM from image generation)
The image service, **CPU only**: the model is never moved to the GPU. It loads on the first call
(about 10 s, from the Hugging Face cache on D:), stays while in use, and is freed after 10 minutes without a call.
It refuses to load while under 2 GB of RAM is free (HTTP 503 "only N GB of RAM is free"): the local model needs it
more; try later. About 1 s an image once loaded.

## Input and output
`image.embed(image)`: `image` is an image name, `"folder/name.png"`, or a Printify mockup address (as image.compare).
Or `image.embed(image_b64=...)`: the image itself, base64, up to 10 MB decoded, for one that isn't in the image store
(2026-10-09: 21 of the owner's 61 rated designs were uploaded as data). One of the two, never both. A print file's
see-through parts count as white. Returns `{"model", "dim", "vector", "ms"}`.

Preprocessing follows the model's own `preprocessor_config.json`: a bilinear resize to 224x224, x/255, then
(x - 0.5) / 0.5 (done in the service: transformers' own processor needs torchvision, which it doesn't have).

## Implementation
`local-services/opendecision-service/embed.py` and `POST /image/embed` (image.py); the tool `mcp-server/tools/image/embed.py`.
Tests: `local-services/opendecision-service/test_embed.py` (the model faked), `mcp-server/tests/test_image_embed.py`.
