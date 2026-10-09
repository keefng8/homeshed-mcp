"""Image providers for image.generate / image.status / image.providers.list. See ../../capabilities/image/providers.md.

Two kinds:
- local-sdcpp: an optional local image service you run yourself (stable-diffusion.cpp), free. Its jobs live there.
- remote HTTP providers (OpenAI, Stability AI, Replicate, fal.ai, Together, xAI): PAID per image, each enabled only
  when its API key is stored (vault.secret by name: the Control Panel's Credentials first, then the environment).
  Never chosen silently: image.generate needs provider=..., IMAGE_DEFAULT_PROVIDER, or require_commercial=True.
  Their jobs run here in a background thread, with the same job model as the local one (a 12-hex job_id that
  image.status reads), and the file plus a .json sidecar (provider, model, licence, cost) are saved under
  IMAGE_OUTPUT_DIR (default DATA_DIR/usage/images).

Switches (Control Panel > Settings > Image generation, saved by runtime_settings on the usage volume, owner token
only): every provider and every model has an on/off switch, and each paid provider a daily image cap. Defaults: the
local models on, every paid provider OFF with a cap of 0, even when its key is stored. A paid provider is usable only
when it's switched on, its model is on, its key is stored and today's cap isn't reached. Clients see the switches
through image.providers.list but can't change them (client tokens only reach /mcp).

Keys are only ever put in a request header. Errors carry an HTTP status and the provider's short reason with URLs and
the key scrubbed out; never the key, a URL with a token, or the request headers.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from paths import data_path

LOCAL = "local-sdcpp"
REQUEST_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
JOB_TIMEOUT_S = 300
MAX_RUNNING = 3            # remote jobs at once; a busy agent gets a clear "busy" instead of a pile-up
MAX_JOBS_KEPT = 50         # in memory; the sidecars on disk keep the rest
MAX_IMAGE_BYTES = 30 * 1024 * 1024
PREVIEW_MAX_BYTES = 2 * 1024 * 1024
_URL = re.compile(r"\b(?:https?|data):\S+", re.IGNORECASE)

OPENAI_TERMS = "https://openai.com/policies/terms-of-use/"
FLUX_SCHNELL = {"name": "Apache-2.0 (FLUX.1 [schnell] weights)", "commercial_use": True,
                "terms_url": "https://huggingface.co/black-forest-labs/FLUX.1-schnell"}
XAI_TERMS = {"name": "xAI Terms of Service (Enterprise): output owned by the customer", "commercial_use": True,
             "terms_url": "https://x.ai/legal/terms-of-service-enterprise"}

# commercial_use: True = outputs may be sold; False = never; "check" = depends on plan/terms, read them first.
# cost_estimate_usd: approximate per 1024px image (2026-10), None when it varies too much to state; always confirm
# on the pricing page.
PROVIDERS: dict[str, dict] = {
    LOCAL: {
        "label": "This PC's GPU (stable-diffusion.cpp)", "key": None, "paid": False,
        "cost_note": "free: runs on your own GPU", "pricing_url": None,
        "default_model": "qwen-image-2.1",
        "models": {
            "qwen-image-2.1": {"license": {"name": "Qwen Research License", "commercial_use": False,
                                           "terms_url": "https://huggingface.co/Qwen"},
                               "installed": True, "cost_estimate_usd": 0.0,
                               "note": "NON-COMMERCIAL: nothing made with it can be sold"},
            "flux.1-schnell": {"license": FLUX_SCHNELL, "installed": False, "cost_estimate_usd": 0.0,
                               "note": "needs its model weights where the service runs"},
        },
    },
    "openai": {
        "label": "OpenAI Images", "key": "OPENAI_API_KEY", "paid": True,
        "cost_note": "paid per image (token-based); see pricing", "pricing_url": "https://openai.com/api/pricing/",
        "default_model": "gpt-image-1-mini",
        "models": {
            "gpt-image-1-mini": {"license": {"name": "OpenAI Terms of Use (you own the output)", "commercial_use": True,
                                             "terms_url": OPENAI_TERMS}, "cost_estimate_usd": None},
            "gpt-image-2": {"license": {"name": "OpenAI Terms of Use (you own the output)", "commercial_use": True,
                                        "terms_url": OPENAI_TERMS}, "cost_estimate_usd": None},
        },
    },
    "stability": {
        "label": "Stability AI", "key": "STABILITY_API_KEY", "paid": True,
        "cost_note": "paid in credits per image (Core about 3 credits)", "pricing_url": "https://platform.stability.ai/pricing",
        "default_model": "core",
        "models": {
            "core": {"license": {"name": "Stability AI API Terms of Service", "commercial_use": "check",
                                 "terms_url": "https://stability.ai/terms-of-use"}, "cost_estimate_usd": 0.03},
            "ultra": {"license": {"name": "Stability AI API Terms of Service", "commercial_use": "check",
                                  "terms_url": "https://stability.ai/terms-of-use"}, "cost_estimate_usd": 0.08},
        },
    },
    "replicate": {
        "label": "Replicate", "key": "REPLICATE_API_TOKEN", "paid": True,
        "cost_note": "paid per image", "pricing_url": "https://replicate.com/pricing",
        "default_model": "black-forest-labs/flux-schnell",
        "models": {"black-forest-labs/flux-schnell": {"license": FLUX_SCHNELL, "cost_estimate_usd": 0.003}},
    },
    "fal": {
        "label": "fal.ai", "key": "FAL_KEY", "paid": True,
        "cost_note": "paid per megapixel", "pricing_url": "https://fal.ai/pricing",
        "default_model": "fal-ai/flux/schnell",
        "models": {"fal-ai/flux/schnell": {"license": FLUX_SCHNELL, "cost_estimate_usd": 0.003}},
    },
    "together": {
        "label": "Together AI", "key": "TOGETHER_API_KEY", "paid": True,
        "cost_note": "paid per megapixel", "pricing_url": "https://www.together.ai/pricing",
        "default_model": "black-forest-labs/FLUX.1-schnell",
        "models": {"black-forest-labs/FLUX.1-schnell": {"license": FLUX_SCHNELL, "cost_estimate_usd": 0.003}},
    },
    "xai": {
        "label": "xAI Grok Imagine", "key": "XAI_API_KEY", "paid": True,
        "cost_note": "flat fee per image", "pricing_url": "https://docs.x.ai/docs/models",
        "default_model": "grok-imagine-image-2.0",
        # The API is under xAI's Enterprise terms: the customer "owns all right, title, and interest in the Output"
        # and xAI assigns its rights to the customer (checked by R&D and here, 2026-10-06). Prices: R&D, live, same day.
        "models": {m: {"license": XAI_TERMS, "cost_estimate_usd": usd}
                   for m, usd in (("grok-imagine-image-2.0", 0.06),
                                  ("grok-imagine-image", 0.02))},
    },
}
# require_commercial with no provider named: the free local model first, then the cheapest paid ones.
COMMERCIAL_ORDER = [LOCAL, "together", "fal", "replicate", "openai"]
# Where a provider's image URLs may be fetched from (it returned them); anything else is refused.
DOWNLOAD_HOSTS = {"xai": ("x.ai",), "replicate": ("replicate.delivery", "replicate.com"),
                  "fal": ("fal.media", "fal.run", "fal.ai"), "openai": (), "together": ("together.ai", "together.xyz"),
                  "stability": ()}

# --- switches (runtime_settings entries; the Settings page draws them) ---------------------------------------------

SETTINGS_GROUP = "Image generation"
CAP_OPTIONS = ["0", "5", "10", "25", "50", "100", "500"]
USAGE_FILE_DEFAULT = "usage/image_daily.json"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def provider_setting(provider: str) -> str:
    return f"image_provider_{_slug(provider)}_enabled"


def model_setting(provider: str, model: str) -> str:
    return f"image_model_{_slug(provider)}_{_slug(model)}_enabled"


def cap_setting(provider: str) -> str:
    return f"image_daily_cap_{_slug(provider)}"


def _build_settings() -> dict:
    out: dict = {}
    for name, p in PROVIDERS.items():
        paid = p["paid"]
        out[provider_setting(name)] = {
            "type": "bool", "default": not paid, "group": SETTINGS_GROUP,
            "label": f"Use {p['label']}" + (" (paid)" if paid else " (free)"),
            "help": (f"Paid per image ({p['cost_note']}). Also needs {p['key']} under Keys and passwords, and a daily "
                     "cap above 0. Off until you switch it on." if paid else
                     "Free image generation on your PC's GPU. Each model has its own switch below."),
        }
        if paid:
            out[cap_setting(name)] = {
                "type": "select", "default": "0", "group": SETTINGS_GROUP, "options": CAP_OPTIONS,
                "option_labels": {o: ("None" if o == "0" else f"{o} a day") for o in CAP_OPTIONS},
                "label": f"{p['label']}: most images a day",
                "help": "Paid images allowed per day (counted when a job starts, so failed ones count too). "
                        "None means it can't be used at all.",
            }
        for mid, m in p["models"].items():
            lic, cu = m["license"], m["license"]["commercial_use"]
            sell = {True: "outputs may be sold", False: "NON-COMMERCIAL: outputs can't be sold",
                    "check": "commercial use depends on the provider's terms: check them"}[cu]
            out[model_setting(name, mid)] = {
                "type": "bool", "default": True, "group": SETTINGS_GROUP,
                "label": f"{p['label']}: {mid}",
                "help": f"{lic['name']}; {sell}." + (" Only used while the provider above is on." if paid else ""),
            }
    return out


SETTINGS = _build_settings()


def _setting(key: str):
    """The live value (runtime_settings re-reads its file when it changes), or the default if the store isn't here."""
    try:
        import runtime_settings
        return runtime_settings.get(key) if key in runtime_settings.SCHEMA else SETTINGS[key]["default"]
    except Exception:  # noqa: BLE001 - a broken settings store means defaults, never paid use
        return SETTINGS[key]["default"]


def provider_enabled(provider: str) -> bool:
    return _setting(provider_setting(provider)) is True


def model_enabled(provider: str, model: str) -> bool:
    return _setting(model_setting(provider, model)) is True


def daily_cap(provider: str) -> int | None:
    """Paid images allowed today; None for the free local provider."""
    if not PROVIDERS[provider]["paid"]:
        return None
    try:
        return max(0, int(_setting(cap_setting(provider))))
    except (TypeError, ValueError):
        return 0


def _usage_file() -> Path:
    return Path(os.environ.get("IMAGE_USAGE_FILE") or data_path(USAGE_FILE_DEFAULT))


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _usage() -> dict:
    try:
        data = json.loads(_usage_file().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("date") == _today() and isinstance(data.get("counts"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"date": _today(), "counts": {}}


def used_today(provider: str) -> int:
    return int(_usage()["counts"].get(provider, 0) or 0)


def _count_one(provider: str) -> None:
    data = _usage()
    data["counts"][provider] = int(data["counts"].get(provider, 0) or 0) + 1
    f = _usage_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, f)


OWNER_HINT = "only the owner can change this: Control Panel > Settings > Image generation"


def unusable_reason(provider: str, model: str | None = None, hint: bool = True) -> str | None:
    """Why provider (and model) can't be used right now, or None if it can. Never mentions a key's value. hint=False
    leaves out where the owner changes it (image.providers.list says that once)."""
    p = PROVIDERS[provider]
    tail = f" ({OWNER_HINT})" if hint else ""
    if not provider_enabled(provider):
        return f"{provider} is switched off{tail}"
    if model is not None and not model_enabled(provider, model):
        return f"{provider}/{model} is switched off{tail}"
    if p["key"] and not configured(provider):
        return f"{provider} isn't configured: add {p['key']} under the Control Panel's Credentials"
    cap = daily_cap(provider)
    if cap is not None:
        if cap == 0:
            return f"{provider}'s daily image cap is 0, so it can't be used{tail}"
        if used_today(provider) >= cap:
            return f"{provider} has reached today's cap of {cap} images{tail}"
    return None


# Tests set this to an httpx.MockTransport.
_TRANSPORT: httpx.BaseTransport | None = None


class ProviderError(RuntimeError):
    """Safe to show: no key, no URL."""


# --- catalogue ---------------------------------------------------------------------------------------------------

def _secret(name: str) -> str | None:
    import vault
    value = vault.secret(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def configured(provider: str) -> bool:
    p = PROVIDERS[provider]
    return True if p["key"] is None else bool(_secret(p["key"]))


def default_provider() -> str | None:
    """IMAGE_DEFAULT_PROVIDER (vault or env), checked against the catalogue."""
    name = (_secret("IMAGE_DEFAULT_PROVIDER") or "").lower()
    if not name:
        return None
    if name not in PROVIDERS:
        raise ProviderError(f"IMAGE_DEFAULT_PROVIDER names an unknown provider; known: {sorted(PROVIDERS)}")
    return name


def model_info(provider: str, model: str) -> dict:
    p = PROVIDERS.get(provider)
    if p is None:
        raise ProviderError(f"unknown image provider {provider!r}; known: {sorted(PROVIDERS)}")
    m = p["models"].get(model)
    if m is None:
        raise ProviderError(f"{provider} has no model {model!r} here; models: {sorted(p['models'])}")
    return m


def is_commercial(provider: str, model: str) -> bool:
    return model_info(provider, model)["license"]["commercial_use"] is True


def local_ready(fetch=None) -> dict[str, bool] | None:
    """Which local models the GPU service says are ready (its GET /image/models), or None if it can't be asked."""
    try:
        from tools.image.generate import base_url
        resp = (fetch or httpx.get)(f"{base_url()}/image/models", timeout=5)
        if resp.status_code != 200:
            return None
        return {k: bool(v.get("ready")) for k, v in (resp.json().get("models") or {}).items()}
    except Exception:  # noqa: BLE001 - a listing must never fail because the PC is off
        return None


def catalogue(ready: dict[str, bool] | None = None) -> list[dict]:
    rows = []
    for name, p in PROVIDERS.items():
        models = []
        for mid, m in p["models"].items():
            row = {"model": mid, "enabled": model_enabled(name, mid), "license": m["license"]["name"],
                   "commercial_use": m["license"]["commercial_use"], "terms_url": m["license"]["terms_url"],
                   "cost_estimate_usd": m.get("cost_estimate_usd")}
            if name == LOCAL:  # remote models are always "installed"
                row["installed"] = bool(ready.get(mid, False)) if ready is not None else m.get("installed", True)
            if mid == p["default_model"]:
                row["default"] = True
            if m.get("note"):
                row["note"] = m["note"]
            models.append(row)
        why = unusable_reason(name, hint=False)
        row = {"provider": name, "enabled": provider_enabled(name),
               "configured": configured(name), "usable": why is None, "paid": p["paid"], "cost_note": p["cost_note"]}
        if p["paid"]:
            row.update(key_name=p["key"], pricing_url=p["pricing_url"], daily_cap=daily_cap(name),
                       used_today=used_today(name))
        if why:
            row["why_not"] = why
        row["models"] = models
        rows.append(row)
    return rows


# --- helpers -----------------------------------------------------------------------------------------------------

def _clean(text: str, key: str | None) -> str:
    text = _URL.sub("<url>", str(text or ""))
    if key:
        text = text.replace(key, "<key>")
    return text.strip()[:200]


def _http() -> httpx.Client:
    kw: dict = {"timeout": REQUEST_TIMEOUT, "follow_redirects": False}
    if _TRANSPORT is not None:
        kw["transport"] = _TRANSPORT
    return httpx.Client(**kw)


def _send(provider: str, key: str, method: str, url: str, **kw) -> httpx.Response:
    try:
        with _http() as c:
            resp = c.request(method, url, **kw)
    except httpx.TimeoutException:
        raise ProviderError(f"{provider} didn't answer in time") from None
    except httpx.HTTPError as exc:
        raise ProviderError(f"{provider} unreachable ({type(exc).__name__})") from None
    if resp.status_code >= 400:
        reason = ""
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            reason = (err.get("message") if isinstance(err, dict) else err) or body.get("detail") or \
                body.get("message") or body.get("errors") or ""
        except (ValueError, AttributeError):
            reason = ""
        hint = {401: " (check the API key)", 402: " (out of credit?)", 403: " (key lacks access)",
                429: " (rate limited or out of quota)"}.get(resp.status_code, "")
        raise ProviderError(f"{provider} returned http {resp.status_code}{hint}" +
                            (f": {_clean(reason, key)}" if reason else ""))
    return resp


def _download(provider: str, url: str) -> tuple[bytes, str]:
    if url.startswith("data:"):
        head, _, data = url.partition(",")
        return base64.b64decode(data), head[5:].split(";")[0] or "image/png"
    host = (urlparse(url).hostname or "").lower()
    allowed = DOWNLOAD_HOSTS.get(provider, ())
    if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in allowed):
        raise ProviderError(f"{provider} returned an image on an unexpected host; not fetched")
    resp = _send(provider, "", "GET", url)
    if len(resp.content) > MAX_IMAGE_BYTES:
        raise ProviderError(f"{provider}'s image is over {MAX_IMAGE_BYTES // 2**20} MB")
    return resp.content, resp.headers.get("content-type", "image/png").split(";")[0]


def _aspect(width: int, height: int, allowed: list[str]) -> str:
    target = width / height
    return min(allowed, key=lambda a: abs(int(a.split(":")[0]) / int(a.split(":")[1]) - target))


def _first_image(provider: str, body: dict) -> tuple[bytes, str]:
    data = (body or {}).get("data") or []
    if not data or not isinstance(data, list):
        raise ProviderError(f"{provider} returned no image")
    item = data[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"]), "image/png"
    if item.get("url"):
        return _download(provider, item["url"])
    raise ProviderError(f"{provider} returned no image")


# --- adapters: (key, model, prompt, width, height, seed, steps) -> (bytes, mime) -------------------------------------

def _openai(key, model, prompt, width, height, seed, steps):
    size = "1024x1024" if abs(width - height) < 64 else ("1536x1024" if width > height else "1024x1536")
    resp = _send("openai", key, "POST", "https://api.openai.com/v1/images/generations",
                 headers={"Authorization": f"Bearer {key}"},
                 json={"model": model, "prompt": prompt, "n": 1, "size": size, "output_format": "png"})
    return _first_image("openai", resp.json())


# xAI takes a shape, not a size: aspect_ratio (R&D found 2 of 3 square requests came back 16:9; a 1:1 request,
# 2026-10-07, came back 1024x1024). The nearest of these to the asked width/height is sent.
XAI_RATIOS = {"1:1": 1.0, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 1.5, "2:3": 2 / 3}


def xai_aspect(width: int, height: int) -> str:
    want = (width or 1) / (height or 1)
    return min(XAI_RATIOS, key=lambda k: abs(XAI_RATIOS[k] - want))


def _xai(key, model, prompt, width, height, seed, steps):
    resp = _send("xai", key, "POST", "https://api.x.ai/v1/images/generations",
                 headers={"Authorization": f"Bearer {key}"},
                 json={"model": model, "prompt": prompt, "n": 1, "response_format": "b64_json",
                       "aspect_ratio": xai_aspect(width, height)})
    return _first_image("xai", resp.json())


def _together(key, model, prompt, width, height, seed, steps):
    body = {"model": model, "prompt": prompt, "n": 1, "width": width, "height": height,
            "steps": min(steps or 4, 12), "response_format": "base64", "output_format": "png"}
    if seed >= 0:
        body["seed"] = seed
    resp = _send("together", key, "POST", "https://api.together.ai/v1/images/generations",
                 headers={"Authorization": f"Bearer {key}"}, json=body)
    return _first_image("together", resp.json())


STABILITY_ASPECTS = ["16:9", "1:1", "21:9", "2:3", "3:2", "4:5", "5:4", "9:16", "9:21"]


def _stability(key, model, prompt, width, height, seed, steps):
    form = {"prompt": prompt, "output_format": "png", "aspect_ratio": _aspect(width, height, STABILITY_ASPECTS)}
    if seed >= 0:
        form["seed"] = str(seed)
    resp = _send("stability", key, "POST", f"https://api.stability.ai/v2beta/stable-image/generate/{model}",
                 headers={"Authorization": f"Bearer {key}", "Accept": "image/*"},
                 data=form, files={"none": ("", b"")})  # multipart/form-data, as the API requires
    mime = resp.headers.get("content-type", "image/png").split(";")[0]
    if not mime.startswith("image/"):
        raise ProviderError("stability returned no image")
    return resp.content, mime


REPLICATE_ASPECTS = ["1:1", "16:9", "21:9", "3:2", "2:3", "4:5", "5:4", "3:4", "4:3", "9:16", "9:21"]


def _replicate(key, model, prompt, width, height, seed, steps):
    auth = {"Authorization": f"Bearer {key}"}
    body = {"input": {"prompt": prompt, "aspect_ratio": _aspect(width, height, REPLICATE_ASPECTS),
                      "output_format": "png", "num_outputs": 1}}
    if seed >= 0:
        body["input"]["seed"] = seed
    resp = _send("replicate", key, "POST", f"https://api.replicate.com/v1/models/{model}/predictions",
                 headers={**auth, "Prefer": "wait=60", "Cancel-After": f"{JOB_TIMEOUT_S}s"}, json=body)
    pred, deadline = resp.json(), time.monotonic() + JOB_TIMEOUT_S
    while pred.get("status") not in ("succeeded", "failed", "canceled") and not pred.get("output"):
        if time.monotonic() > deadline:
            raise ProviderError(f"replicate didn't finish within {JOB_TIMEOUT_S}s")
        time.sleep(_POLL_S)
        pid = str(pred.get("id") or "")
        if not re.fullmatch(r"[a-z0-9]{8,64}", pid):
            raise ProviderError("replicate returned no prediction id")
        pred = _send("replicate", key, "GET", f"https://api.replicate.com/v1/predictions/{pid}", headers=auth).json()
    if pred.get("status") in ("failed", "canceled"):
        raise ProviderError(f"replicate prediction {pred.get('status')}: {_clean(pred.get('error'), key)}")
    out = pred.get("output")
    url = out[0] if isinstance(out, list) and out else out
    if not isinstance(url, str):
        raise ProviderError("replicate returned no image")
    return _download("replicate", url)


_POLL_S = 2.0


def _fal(key, model, prompt, width, height, seed, steps):
    body = {"prompt": prompt, "image_size": {"width": width, "height": height}, "num_images": 1,
            "num_inference_steps": min(steps or 4, 12), "output_format": "png", "sync_mode": True}
    if seed >= 0:
        body["seed"] = seed
    resp = _send("fal", key, "POST", f"https://fal.run/{model}", headers={"Authorization": f"Key {key}"}, json=body)
    images = (resp.json() or {}).get("images") or []
    if not images or not images[0].get("url"):
        raise ProviderError("fal returned no image")
    return _download("fal", images[0]["url"])


ADAPTERS = {"openai": _openai, "stability": _stability, "replicate": _replicate, "fal": _fal,
            "together": _together, "xai": _xai}


# --- remote jobs (same shape as the GPU service's) -----------------------------------------------------------------

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
JOB_ID = re.compile(r"^[a-f0-9]{12}$")


def output_dir() -> Path:
    return Path(os.environ.get("IMAGE_OUTPUT_DIR") or data_path("usage/images"))


def _public(job: dict) -> dict:
    out = {k: v for k, v in job.items() if not k.startswith("_")}
    out["elapsed_s"] = round((job.get("finished_at") or time.time()) - job["started_at"], 1)
    return out


def _sidecar(job: dict) -> None:
    d = output_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{job['job_id']}.json.tmp"
    tmp.write_text(json.dumps(_public(job), indent=1), encoding="utf-8")
    os.replace(tmp, d / f"{job['job_id']}.json")


def sniff_mime(data: bytes) -> str | None:
    """The image type from its first bytes. A provider's own label can be wrong (KB-0066: xAI sent JPEG bytes that
    were saved as .png, and a client refused them as a bad image)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _run(job: dict, key: str) -> None:
    try:
        data, mime = ADAPTERS[job["provider"]](key, job["model"], job["prompt"], job["width"], job["height"],
                                               job["seed"], job["steps"])
        if not data:
            raise ProviderError(f"{job['provider']} returned an empty image")
        mime = sniff_mime(data) or mime
        ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{job['provider']}-{job['job_id']}.{ext}"
        d = output_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
        job.update(status="done", progress="done", saved_as=name, bytes=len(data), mime=mime)
    except ProviderError as exc:
        job.update(status="failed", progress="failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a job thread must always end in a state
        job.update(status="failed", progress="failed", error=f"unexpected error ({type(exc).__name__})")
    finally:
        job["finished_at"] = time.time()
        try:
            _sidecar(job)
        except OSError:
            job["sidecar_error"] = "could not save the .json sidecar"


def start_remote(provider: str, model: str, prompt: str, width: int, height: int, seed: int, steps: int,
                 background: bool = True) -> dict:
    p = PROVIDERS[provider]
    key = _secret(p["key"])
    if not key:
        raise ProviderError(f"{provider} isn't configured: add {p['key']} under the Control Panel's Credentials")
    m = model_info(provider, model)
    with _lock:
        why = unusable_reason(provider, model)
        if why:
            raise ProviderError(why)
        if sum(1 for j in _jobs.values() if j["status"] == "running") >= MAX_RUNNING:
            raise ProviderError(f"{MAX_RUNNING} paid image jobs are already running; try again shortly")
        try:
            _count_one(provider)
        except OSError:
            raise ProviderError("couldn't record today's image count, so the paid call wasn't made") from None
        job_id = secrets.token_hex(6)
        job = {"job_id": job_id, "status": "running", "progress": f"sent to {provider}", "provider": provider,
               "model": model, "prompt": prompt, "width": width, "height": height, "steps": steps, "seed": seed,
               "saved_as": None, "error": None, "paid": True, "cost_estimate_usd": m.get("cost_estimate_usd"),
               "cost_note": p["cost_note"], "pricing_url": p["pricing_url"],
               "license": m["license"]["name"], "license_info": dict(m["license"]),
               "commercial_use": m["license"]["commercial_use"], "timeout_s": JOB_TIMEOUT_S,
               "started_at": time.time(), "finished_at": None}
        _jobs[job_id] = job
        for old in sorted(_jobs, key=lambda k: _jobs[k]["started_at"])[:-MAX_JOBS_KEPT]:
            if _jobs[old]["status"] != "running":
                del _jobs[old]
    if background:
        threading.Thread(target=_run, args=(job, key), daemon=True, name=f"image-{job_id}").start()
    else:
        _run(job, key)
    return _public(job)


def remote_status(job_id: str, include_image: bool = False) -> dict | None:
    """A remote job's state (memory first, then its sidecar), or None if it isn't a remote job."""
    job = _jobs.get(job_id)
    if job is not None:
        out = _public(job)
    else:
        f = output_dir() / f"{job_id}.json"
        if not JOB_ID.match(job_id) or not f.is_file():
            return None
        try:
            out = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    if include_image and out.get("status") == "done" and out.get("saved_as"):
        path = output_dir() / out["saved_as"]
        if path.is_file() and path.stat().st_size <= PREVIEW_MAX_BYTES:
            out["image_b64"], out["image_mime"] = base64.b64encode(path.read_bytes()).decode(), out.get("mime")
        else:
            out["image_note"] = f"image over {PREVIEW_MAX_BYTES // 2**20} MB: not inlined; it's at {out['saved_as']}"
    return out
