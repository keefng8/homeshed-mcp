"""image.generate. See ../../capabilities/image/generate.md and providers.md.

Local (default, free): starts a job on the optional local image service you run yourself (IMAGE_BASE_URL,
stable-diffusion.cpp) and returns straight away with a job_id. It NEVER blocks for the minutes an image takes; check
progress with image.status. The default local model is under a NON-COMMERCIAL license, returned with every job.

Remote (paid, opt-in): provider="openai" | "stability" | "replicate" | "fal" | "together" | "xai", or the
IMAGE_DEFAULT_PROVIDER setting, or require_commercial=True with nothing named (tools/image/providers.py). Same job
model: a job_id at once, the image via image.status.
"""
from __future__ import annotations

import os
import threading

import httpx

from registry import tool
from tools.image import providers as prov
from tools.local_ai.ask import LocalAIError, ask

# Qwen officially recommends rewriting short prompts into detailed descriptions before
# generating (Qwen-Image-2.1 README). Done here by the local_ai.ask delegator: free, a few seconds.
ENHANCE_INSTRUCTION = (
    "Rewrite this image-generation prompt as ONE detailed paragraph of {words} words: subject, "
    "composition, setting, lighting, colour palette, mood, style, and fine detail. Fix spelling mistakes. Keep the "
    "user's intent exactly; don't add people, objects or visible text they didn't ask for; keep any words to be shown "
    "in the image exactly as written, in quotes. Write plain descriptive sentences, not a list of tags. Output only "
    "the rewritten prompt, with no preamble or quotes.\n\nPrompt: "
)
# FLUX.1-schnell reads at most 256 T5 tokens (Hugging Face diffusers' FLUX docs: "max_sequence_length cannot be more
# than 256" for schnell); about 1,000 characters of English. Longer prompts lose their end, so the rewrite is kept
# shorter for it, and a long prompt of the owner's gets a warning (the owner, 2026-10-07, #65: "info on char limits").
FLUX_MAX_CHARS = 1000
WORDS = {"flux.1-schnell": "40-90"}
# The rewrite may take no longer than this: a slow or busy model must never make image.generate fail (the owner,
# 2026-10-07, #65: queuing failed with an HTML error while the local model was off for training).
ENHANCE_BUDGET_S = 15


def _enhance(prompt: str, model: str) -> tuple[str | None, str | None, str | None]:
    """(rewritten prompt or None, the backend that wrote it, why it wasn't used). Never raises, never waits longer
    than ENHANCE_BUDGET_S (the rewrite carries on in its thread and its answer is dropped)."""
    out: dict = {}
    done = threading.Event()

    def run():
        try:
            out["r"] = ask(ENHANCE_INSTRUCTION.format(words=WORDS.get(model, "60-120")) + prompt.strip(),
                           max_tokens=300, temperature=0.4)
        except LocalAIError as exc:
            out["e"] = str(exc)
        finally:
            done.set()
    threading.Thread(target=run, daemon=True).start()
    done.wait(ENHANCE_BUDGET_S)
    if "r" not in out:
        return None, None, (f"{out['e']}; used the original prompt" if "e" in out else
                            f"the rewrite took over {ENHANCE_BUDGET_S} s; used the original prompt")
    candidate = out["r"]["text"].strip().strip('"')
    limit = FLUX_MAX_CHARS if model == "flux.1-schnell" else 2000
    if not 20 <= len(candidate) <= limit:
        return None, None, "rewrite was empty or too long; used the original prompt"
    return candidate, out["r"].get("backend"), None


class ImageError(RuntimeError):
    """Safe-to-return failure message. Never includes the backend URL or a key."""


def base_url() -> str:
    base = os.environ.get("IMAGE_BASE_URL") or os.environ.get("LOCAL_AI_DECIDE_BASE_URL")
    if not base:
        raise ImageError("image capabilities are not configured: set IMAGE_BASE_URL")
    return base.rstrip("/")


def raise_for_backend(resp) -> None:
    if resp.status_code == 200:
        return
    try:
        detail = resp.json().get("detail", "")
    except Exception:
        detail = ""
    raise ImageError(f"image backend returned http {resp.status_code}" + (f": {detail}" if detail else ""))


def choose(provider: str, model: str, require_commercial: bool) -> tuple[str, str, bool]:
    """(provider, model, auto_selected). Remote providers only when named, set as IMAGE_DEFAULT_PROVIDER, or picked
    because require_commercial asked for a commercial-safe one and the free local one can't give it. A provider or
    model that's switched off (or a paid one without its key or over its daily cap) is refused, never picked."""
    try:
        named = (provider or "").strip().lower()
        from_default = not named and prov.default_provider() is not None
        name = named or prov.default_provider()
        if not name and require_commercial and not model:
            ready = prov.local_ready() or {}
            for cand in prov.COMMERCIAL_ORDER:
                if cand == prov.LOCAL:
                    local = next((m for m in prov.PROVIDERS[cand]["models"]
                                  if prov.is_commercial(cand, m) and ready.get(m)
                                  and prov.unusable_reason(cand, m) is None), None)
                    if local:
                        return cand, local, True
                else:
                    mid = prov.PROVIDERS[cand]["default_model"]
                    if prov.is_commercial(cand, mid) and prov.unusable_reason(cand, mid) is None:
                        return cand, mid, True
            raise ImageError("require_commercial: no commercial-safe image provider is usable. Install FLUX.1-schnell "
                             "locally, or switch on a paid one (Control Panel > Settings > Image generation, owner "
                             "only) with a daily cap above 0 and its key: " + ", ".join(
                                 f"{c} ({prov.PROVIDERS[c]['key']})" for c in prov.COMMERCIAL_ORDER[1:]))
        name = name or prov.LOCAL
        model = (model or "").strip() or prov.PROVIDERS.get(name, {}).get("default_model", "")
        info = prov.model_info(name, model)
        if require_commercial and info["license"]["commercial_use"] is not True:
            how = ("NON-COMMERCIAL" if info["license"]["commercial_use"] is False
                   else "not confirmed for commercial use (check its terms)")
            raise ImageError(f"require_commercial: {name}/{model} is {how} under {info['license']['name']}; "
                             "pick a commercial-safe model (image.providers.list shows them)")
        why = prov.unusable_reason(name, model)
        if why:
            raise ImageError(why + (" (it's the IMAGE_DEFAULT_PROVIDER setting)" if from_default else ""))
        return name, model, False
    except prov.ProviderError as exc:
        raise ImageError(str(exc)) from None


@tool(name="generate", category="image", doc="image/generate.md")
def generate(prompt: str, width: int = 768, height: int = 768, steps: int = 0, seed: int = -1,
             model: str = "", enhance_prompt: bool = True, upscale: bool = False, provider: str = "",
             require_commercial: bool = False, folder: str = "", paid_fallback: bool = False,
             negative_prompt: str = "", default_negative: bool = True) -> dict:
    """Start an image generation job. Returns in about a second with {"job_id", "status": "running", ...}; check
    it with image.status(job_id). Don't wait in a loop: tell the user it's generating. image.queue shows whether the
    image service is busy and what's waiting.

    Default: FREE local generation on your own local image service (stable-diffusion.cpp), one job at a time (later ones wait in line, status "queued", up to 20), killed
    after 900s. Its default model qwen-image-2.1 is NON-COMMERCIAL. Remote providers are PAID per image and only
    used when asked for: provider=..., the IMAGE_DEFAULT_PROVIDER setting, or require_commercial=True. Each provider
    and model has an owner-only on/off switch (paid ones start OFF, with a daily cap of 0); image.providers.list
    shows which are usable.

    Args:
        prompt: what to draw (1-2000 chars).
        width, height: 256-1536, divisible by 32 locally. Remote providers map them to their nearest size/aspect.
        steps: 1-60 locally; 0 uses the model default. FLUX-schnell providers cap it at 12.
        seed: -1 random; reuse a returned seed to reproduce (not every provider supports seeds).
        model: empty = the provider's default (image.providers.list shows models and their licences).
        enhance_prompt: first expand the prompt with the free local model delegator. If that fails, the original
            prompt is used and "enhance_error" says why.
        upscale: local only: ESRGAN 4x after generation.
        provider: "local-sdcpp" (default) or a configured paid one: openai, stability, replicate, fal, together, xai.
        require_commercial: refuse models whose outputs can't be sold (non-commercial, or "check" terms). With no
            provider named: the free local FLUX.1-schnell if installed and on, else the first usable (switched
            on, key stored, under its daily cap) of together, fal, replicate, openai (PAID; "auto_selected" is true).
        folder: local only: save into this folder of the images (made if new; letters, numbers, spaces, - and _,
            up to 40). Empty saves to the main images folder.
        paid_fallback: local only: if the GPU can't take the image now (too hot, short of RAM or GPU memory, or
            lent out), run it on xAI grok-imagine-image instead (PAID, about $0.02, commercial use allowed) when xAI
            is switched on, has its key and is under its owner-set daily cap; "fell_back" says so. Otherwise it waits
            in the local line (retried every minute for up to 30 min). Not with upscale or folder.
        negative_prompt: local only: what to steer away from (up to 1000 characters). It replaces the built-in list
            (grid pattern, lines, stripes, scanlines, banding, checkerboard, tiling seams, jpeg artifacts, noise,
            blurry, low quality, watermark). The paid fallback doesn't use it.
        default_negative: False drops the built-in list when no negative_prompt is given, e.g. for a design that
            should have stripes or lines (a striped sunset).

    Returns:
        The job: {"job_id", "status", "progress", "provider", "model", "prompt", "width", "height", "seed",
        "saved_as", "license", "license_info": {name, commercial_use, terms_url}, "commercial_use", "paid",
        "cost_estimate_usd", "timeout_s", "original_prompt", "enhanced_by", "enhance_error", "auto_selected"}.
    """
    if not prompt or not prompt.strip():
        raise ImageError("prompt must be non-empty")
    if len(prompt) > 2000:
        raise ImageError("prompt must be at most 2000 characters")
    neg = (negative_prompt or "").strip()
    if len(neg) > 1000:
        raise ImageError("negative_prompt must be at most 1000 characters")
    name, model, auto = choose(provider, model, require_commercial)
    info = prov.model_info(name, model)
    if name == prov.LOCAL:
        base = base_url()
    elif upscale or folder.strip() or neg:
        raise ImageError("upscale, folder and negative_prompt are only available on the local provider")
    original, enhanced_by, enhance_error = prompt, None, None
    if enhance_prompt:
        rewritten, enhanced_by, enhance_error = _enhance(prompt, model)
        prompt = rewritten or prompt
    extra = {"original_prompt": original, "enhanced_by": enhanced_by, "enhance_error": enhance_error,
             "auto_selected": auto}
    if model == "flux.1-schnell" and len(prompt) > FLUX_MAX_CHARS:
        extra["prompt_warning"] = (f"FLUX.1-schnell reads about the first {FLUX_MAX_CHARS} characters (256 tokens); "
                                   "the rest of this prompt is ignored")
    if name != prov.LOCAL:
        try:
            return {**prov.start_remote(name, model, prompt, width, height, seed, steps), **extra}
        except prov.ProviderError as exc:
            raise ImageError(str(exc)) from None
    try:
        resp = httpx.post(f"{base}/image/generate", timeout=30, json={
            "prompt": prompt, "model": model, "width": width, "height": height,
            "steps": steps or None, "seed": seed, "upscale": upscale,
            # kept in the image's record, so its history shows the wording the person typed too
            "original_prompt": original if enhanced_by else None,
            # no key = the service's built-in list; "" = none (striped designs, 2026-10-07)
            **({"negative_prompt": neg} if neg else {} if default_negative else {"negative_prompt": ""}),
            **({"folder": folder.strip()} if folder.strip() else {})})
    except httpx.HTTPError as exc:
        raise ImageError(f"image backend unreachable ({type(exc).__name__})") from None
    raise_for_backend(resp)
    job = resp.json()
    if paid_fallback and _held_by_gpu(job) and not upscale and not folder.strip():
        moved = _to_paid(base, job, prompt, width, height, seed, steps)
        if moved:
            return {**moved, **extra}
    return {**job, "provider": prov.LOCAL, "model": model, "paid": False, "cost_estimate_usd": 0.0,
            "license_info": dict(info["license"]), "commercial_use": info["license"]["commercial_use"], **extra}


# The paid fallback (the owner, 2026-10-07: "go ahead", after a batch of 10 failed on "1.3GB free"): xAI's cheapest
# model, about $0.02 an image, commercial use allowed; the owner's xAI switch and daily cap still apply.
FALLBACK_PROVIDER, FALLBACK_MODEL = "xai", "grok-imagine-image"


def _held_by_gpu(job: dict) -> bool:
    """The local job is waiting because the GPU can't take it now (safeguard: heat, RAM, GPU memory; or the GPU is
    lent out), not just behind another image."""
    p = str(job.get("progress") or "")
    return job.get("status") == "queued" and p.startswith("waiting: ") and "image before it" not in p


def _to_paid(base: str, job: dict, prompt: str, width: int, height: int, seed: int, steps: int) -> dict | None:
    """Cancel the held local job and run it on the fallback provider. None (the local job stays in line) when that
    provider isn't usable (switched off, no key, over its daily cap) or the local job can't be cancelled."""
    try:
        if prov.unusable_reason(FALLBACK_PROVIDER, FALLBACK_MODEL):
            return None
    except prov.ProviderError:
        return None
    try:
        httpx.post(f"{base}/image/jobs/{job['job_id']}/cancel", timeout=10).raise_for_status()
    except httpx.HTTPError:
        return None  # it may have just started locally: leave it
    held = str(job.get("progress"))[len("waiting: "):]
    try:
        out = prov.start_remote(FALLBACK_PROVIDER, FALLBACK_MODEL, prompt, width, height, seed, steps)
    except prov.ProviderError as exc:
        raise ImageError(f"the local GPU couldn't take it ({held}) and the paid fallback failed: {exc}") from None
    return {**out, "fell_back": f"local GPU couldn't take it ({held}); ran on {FALLBACK_PROVIDER} instead"}
