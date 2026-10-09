# Image — Generate

## ID
`image.generate` (starts a job) and `image.status` (checks it)

## Purpose
**Providers (2026-10-03):** besides the free local backend below, `image.generate` can use a commercial-safe local
FLUX.1-schnell and opt-in PAID providers (OpenAI, Stability AI, Replicate, fal.ai, Together, xAI), chosen with
`provider` / `model` / `require_commercial`. Each provider and model has an owner-only switch (paid ones start off,
with a daily cap of 0): Control Panel > Settings > Image generation. See [providers.md](providers.md) and
`image.providers.list`. The rest of
this page is the local backend.

Free, fully local image generation (`local-sdcpp`): an optional local image service you run yourself on your own
GPU (stable-diffusion.cpp). There's no API and no per-image cost. Without it, use a paid provider on your own key.
The Control Panel's Image tab has a panel for it.

## License: read this first
The default model, **Qwen-Image-2.1, is under the Qwen RESEARCH license: NON-COMMERCIAL,
research/evaluation only.** Trying it out is fine; nothing generated with it can be part of
anything sold. The license string comes back with every result, plus `license_info` and `commercial_use`.
For anything to be sold, use `require_commercial=true`: it refuses Qwen and picks a commercial-safe model
(local `flux.1-schnell`, Apache-2.0, once installed: `MODELS` in `image.py`; or a configured paid provider).

## Job model: why nothing ever waits (added 2026-09-25)
An image takes minutes, and nothing should sit waiting on it. So `image.generate` **starts a job and returns in about a second** with a
`job_id`. `image.status(job_id)` reports `status` (running/done/failed), `progress` (parsed live
from sd.cpp: "reading prompt", "step 7/20", "decoding image"), `elapsed_s` and `error`.

**Guards:**
- Each job is **killed at `timeout_s` (900s)** and marked failed.
- One job at a time; a second request gets **409** at once rather than queueing.
- The dashboard stops tracking a minute after the job's own limit.
- Agents should **not** loop on `image.status`. Start the job, tell the user, and let the
  dashboard's Image tab show the result.

## Parameters
`image.generate`:
| Name | Type | Default | Description |
|---|---|---|---|
| `prompt` | string | — | Required, 1-2000 chars. |
| `width` / `height` | integer | `768` | 256-1536, divisible by 32. 512 is fastest. |
| `steps` | integer | `0` | 1-60; `0` = model default (20). |
| `seed` | integer | `-1` | Random if -1; reuse a returned seed to reproduce. |
| `model` | string | `""` | Empty = the provider's default (`qwen-image-2.1` locally). Local: `qwen-image-2.1`, `flux.1-schnell`. |
| `provider` | string | `""` | Empty = `IMAGE_DEFAULT_PROVIDER` or `local-sdcpp`. Paid: `openai`, `stability`, `replicate`, `fal`, `together`, `xai`. |
| `require_commercial` | boolean | `false` | Refuse models whose outputs can't be sold; with nothing named, auto-pick a commercial-safe one (may be PAID). |
| `enhance_prompt` | boolean | `true` | Rewrite the prompt into a detailed 60-120 word description first, using the `local_ai.ask` delegator. Qwen's README officially recommends prompt rewriting. If it fails, the original is used and `enhance_error` says why. |
| `upscale` | boolean | `false` | Local only. RealESRGAN x4plus (BSD-3) after generation, e.g. 1024 to 4096px, via sd.cpp `--upscale-model`. |
| `negative_prompt` | string | `""` | Local only, up to 1000 chars: what to steer away from. Replaces the built-in list (grid pattern, lines, stripes, scanlines, banding, checkerboard, tiling seams, jpeg artifacts, noise, blurry, low quality, watermark). The paid fallback doesn't use it. |
| `default_negative` | boolean | `true` | `false` drops the built-in list when no `negative_prompt` is given: for designs that should have stripes or lines (2026-10-07: the list fought a striped-sunset design). |

`image.status`: `job_id` (12 hex chars; anything else is rejected before any HTTP call) and
`include_image` (default false; adds the base64 PNG, about 1MB. Only the dashboard sets it).

## Returns
The job: `{job_id, status, progress, prompt, width, height, steps, seed, saved_as, license,
timeout_s, elapsed_s, error}`, plus `provider`, `model`, `paid`, `cost_estimate_usd`, `license_info`,
`commercial_use`, `auto_selected` from `image.generate`. Paid providers' images are saved under `IMAGE_OUTPUT_DIR`
(providers.md). Local images are saved in the local image service's own `outputs/` folder.

`paid_fallback=true` (2026-10-07): when the local GPU can't take the image now (too hot, short of RAM or GPU memory,
or lent out), the local job is cancelled and the image runs on xAI `grok-imagine-image` (paid, about $0.02,
commercial use allowed), with `fell_back` saying why. It needs xAI switched on, its key and room under its daily cap;
otherwise the job waits in the local line, retried every minute for up to 30 minutes.

## Quality notes (2026-09-25)
Qwen-Image-2.1 is native **2048px**. The first 512px/8-step test was recognisable but soft,
so the dashboard now defaults to 1024px and 20 steps, with prompt enhancement on and upscale
optional. With `include_image`, status returns a **<=1024px JPEG preview** (`image_b64`,
`image_mime`, `final_width`/`final_height`) rather than the full PNG. A 4x upscale is 4096px and
20MB+, too heavy for MCP. The full file is `GET /image/files/{name}` on the GPU service (strict
name regex), proxied by the dashboard as `/api/image/file/{name}`.

## How it works
- **Service:** the optional local image service answers `/image/generate`, `/image/jobs` and
  `/image/files/{name}` at `IMAGE_BASE_URL`. It is not part of this repo: you run it yourself.
- **Engine:** [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) (MIT). It runs
  as a **subprocess per image, not a resident server**, so a GPU shared with a local LLM gets its
  memory back after each image. `--offload-to-cpu` keeps weights in system RAM and streams them to
  the GPU.
- **Files:** the service keeps `bin/`, `models/` and `outputs/` (about 10GB) outside the repo:
  - `qwen_image_2.1-Q4_K.gguf` (3.9GB)
  - `Qwen3VL-8B-Instruct-Q4_K_M.gguf` text encoder (4.7GB)
  - `qwen_image_2.1_vae_bf16.safetensors` (0.6GB)

## Errors
`ImageError`: empty prompt, unconfigured, backend unreachable, or a non-200 status with the
backend's reason. Examples: 400 bad size/steps, 409 busy, 503 model files not installed, 504
over 900s. Messages never include the backend URL.

## Config
`IMAGE_BASE_URL`, falling back to `LOCAL_AI_DECIDE_BASE_URL` (same service).

## Implementation
`mcp-server/tools/image/generate.py`, `status.py`, `providers.py`. Tests: `mcp-server/tests/test_image_generate.py`,
`test_image_providers.py`.
