# Image — Providers

## ID
`image.providers.list` (read). Selection and licence rules for `image.generate` / `image.status`.

## Purpose
The default local model (Qwen-Image-2.1) is NON-COMMERCIAL. So that you can sell images,
`image.generate` can also use commercially licensed models: a free local FLUX.1-schnell (Apache-2.0, once its weights
are installed) and paid HTTP providers. Added 2026-10-03. `image.providers.list` says what exists, what's configured,
what each costs and whether its outputs may be sold.

## Providers
| Provider | Key name (vault) | Models | Licence of outputs | commercial_use | Cost (approx., 2026-10) |
|---|---|---|---|---|---|
| `local-sdcpp` | none | `qwen-image-2.1` (default) | Qwen Research License | **false** | free |
| `local-sdcpp` | none | `flux.1-schnell` (not installed yet) | Apache-2.0 | true | free |
| `openai` | `OPENAI_API_KEY` | `gpt-image-1-mini` (default), `gpt-image-2` | OpenAI Terms of Use: you own the output | true | token-based, see pricing |
| `stability` | `STABILITY_API_KEY` | `core` (default), `ultra` | Stability AI API Terms | **check** | ~$0.03 / ~$0.08 |
| `replicate` | `REPLICATE_API_TOKEN` | `black-forest-labs/flux-schnell` | Apache-2.0 | true | ~$0.003 |
| `fal` | `FAL_KEY` | `fal-ai/flux/schnell` | Apache-2.0 | true | ~$0.003 per megapixel |
| `together` | `TOGETHER_API_KEY` | `black-forest-labs/FLUX.1-schnell` | Apache-2.0 | true | ~$0.003 per megapixel |
| `xai` | `XAI_API_KEY` | `grok-imagine-image-2.0` | xAI Terms of Service | **check** | flat per image, see pricing |

`commercial_use`: `true` = outputs may be sold; `false` = never; `check` = depends on the plan/terms, read them before
selling (and `require_commercial` treats it as not allowed). Each entry carries a `terms_url`. Costs are estimates
only; the provider's pricing page (`pricing_url`) is the truth.

## Switches (added 2026-10-03, "best to toggle what's allowed to be used")
Every provider and every model has an on/off switch, and each paid provider a **daily image cap**. They're
runtime settings (`runtime_settings.py`, saved in `usage/settings.json` on the tool server's volume), so they change
without a restart or redeploy:
- **Where:** Control Panel > **Settings > AI and tools > Image generation** (the Settings page draws the rows from
  the tool server's `GET /settings`; a change is `PUT /settings`). Owner only: `/settings` takes the owner token,
  and client tokens only ever reach `/mcp`.
- **Defaults:** local-sdcpp on, with both its models on (FLUX still needs its weights); **every paid provider off,
  with a cap of 0 ("None")**, even when its key is stored.
- A paid provider is usable only when **it's on, the model is on, its key is stored, and today's count is under its
  cap** (caps: None, 5, 10, 25, 50, 100, 500 a day). The count is per provider per day (server date), kept in
  `IMAGE_USAGE_FILE` (default `usage/image_daily.json`), and counted when a job starts, so failed calls count too.
- Clients read the state through `image.providers.list`: `enabled` (provider and model), `usable`, `why_not`,
  `daily_cap`, `used_today`. They can't change it.
- Setting keys: `image_provider_<provider>_enabled`, `image_model_<provider>_<model>_enabled`,
  `image_daily_cap_<provider>` (non-alphanumerics become `_`, e.g. `image_model_local_sdcpp_qwen_image_2_1_enabled`).
- If the settings store can't be read, every switch falls back to its default: paid stays off.

## Selection rules (image.generate)
1. `provider` named: use it (and `model`, or its default). It's refused, with the reason, if it or the model is
   switched off, a paid one has no key, or its daily cap is 0 or used up.
2. Else the `IMAGE_DEFAULT_PROVIDER` setting (vault or env), if set: same checks; a switched-off default is refused
   (with a note that it's the default setting), never quietly replaced.
3. Else `local-sdcpp` (free). **A paid provider is never chosen silently.**
4. `require_commercial=true` refuses any model whose `commercial_use` isn't `true`, with a clear message. With no
   provider or model named, it picks the free local FLUX.1-schnell if the GPU service reports it ready and it's on,
   else the first **usable** of together, fal, replicate, openai: **a paid call**, and the result says
   `"auto_selected": true`, `"paid": true`. Switched-off, keyless or capped providers are skipped.

## Job model (same for every provider)
`image.generate` returns at once with a 12-hex `job_id`; `image.status(job_id)` reports running/done/failed. Local
jobs live in the GPU service. Paid jobs run in a background thread in this server: at most 3 at once, each request
timed out at 120 s (10 s to connect), the whole job at 300 s. Results carry `provider`, `model`, `license`,
`license_info {name, commercial_use, terms_url}`, `commercial_use`, `paid`, `cost_estimate_usd`.

## Where paid images go
`IMAGE_OUTPUT_DIR` (default `DATA_DIR/usage/images`, the persistent `usage` volume in Docker), as
`YYYYmmdd-HHMMSS-<provider>-<job_id>.<ext>`, plus `<job_id>.json`: the job with its licence and cost, so status works
after a restart and the licence stays with the file. Remote image URLs are fetched only over https from that
provider's own hosts (anything else is refused), capped at 30 MB. `image.status(include_image=true)` inlines files up
to 2 MB.

## Safety
Keys come from `vault.secret` by name and go only into a request header. Errors carry the HTTP status, a hint (401
check the key, 402 out of credit, 429 rate limited) and the provider's short reason, with every URL and the key
scrubbed; never headers or tokens. `image.providers.list` shows `configured: true/false` and the key's NAME only.

## Implementation
`mcp-server/tools/image/providers.py` (catalogue, adapters, remote jobs), `providers_list.py`, `generate.py`,
`status.py`. Tests (all offline, httpx.MockTransport): `mcp-server/tests/test_image_providers.py`. Endpoints, as
verified 2026-10-03:
- OpenAI `POST https://api.openai.com/v1/images/generations` (Bearer), `data[0].b64_json`.
- xAI `POST https://api.x.ai/v1/images/generations` (Bearer), `response_format: b64_json`.
- Together `POST https://api.together.ai/v1/images/generations` (Bearer), `response_format: base64`.
- fal `POST https://fal.run/fal-ai/flux/schnell` (`Authorization: Key ...`), `sync_mode: true` gives a data URI.
- Stability `POST https://api.stability.ai/v2beta/stable-image/generate/{core|ultra}` (Bearer, multipart,
  `Accept: image/*`), raw image bytes.
- Replicate `POST https://api.replicate.com/v1/models/black-forest-labs/flux-schnell/predictions` (Bearer,
  `Prefer: wait=60`), then polls `GET /v1/predictions/{id}`; output is a URL on replicate.delivery.
