"""ElevenLabs text to speech for tts.* (provider="elevenlabs"): a second voice provider for an app's audiobooks.

The owner's paid plan, its key in the vault as ELEVENLABS_API_KEY. A piece is refused before it's sent when it would go
past the plan's characters this billing period, so nothing ever goes into overage. Facts from ElevenLabs' API pages
(checked 2026-10-29): POST /v1/text-to-speech/{voice_id} with the xi-api-key header, output_format mp3_44100_128 (any
plan), body text + model_id (default eleven_multilingual_v2) + voice_settings; GET /v2/voices (page_size up to 100,
next_page_token, has_more); GET /v1/user/subscription (character_count, character_limit,
next_character_count_reset_unix). Our own code: no ElevenLabs SDK.
"""
from __future__ import annotations

import re
import threading
import time

import httpx

from tools.commerce._http import clean, secret
from tools.tts._google import TTSError

HOST = "api.elevenlabs.io"
KEY_NAME = "ELEVENLABS_API_KEY"
OUTPUT = "mp3_44100_128"
OUTPUTS = ("mp3_44100_128", "mp3_44100_192")  # 192 kbps needs the Creator tier or above (ElevenLabs refuses it below)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,80}$")
MAX_CONTEXT_CHARS = 1000  # previous_text / next_text: a sentence or two either side is all continuity needs
DEFAULT_MODEL = "eleven_multilingual_v2"
MAX_CHARS = 5000
MAX_ANSWER_BYTES = 30 * 1024 * 1024
TIMEOUT_S = 180.0
VOICES_TTL_S = 3600
MAX_VOICE_PAGES = 10
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{10,40}$")
MODEL_RE = re.compile(r"^eleven_[a-z0-9_]{2,40}$")
CAP_REACHED = "TTS_CAP_REACHED"
GUIDE = "add the ElevenLabs API key under Settings > Credentials as " + KEY_NAME
HINTS = {401: " (the key was refused)", 402: " (the plan doesn't allow this: check the subscription)",
         422: " (ElevenLabs refused the request's settings)", 429: " (too many at once: wait a moment)"}

# Tests set this to an httpx.MockTransport so no request ever leaves the process.
_TRANSPORT: httpx.BaseTransport | None = None
_voices_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()
_reserved = 0  # characters sent but not yet counted by ElevenLabs, so two pieces at once can't both squeeze under


def key() -> str:
    value = secret(KEY_NAME)
    if not value:
        raise TTSError(f"ElevenLabs isn't set up: {GUIDE}")
    return value


def request(method: str, path: str, *, params: dict | None = None, json: dict | None = None,
            audio: bool = False):
    """One call to ElevenLabs' own host: its JSON answer, or the audio bytes when audio=True."""
    k = key()
    kwargs: dict = {"timeout": httpx.Timeout(TIMEOUT_S, connect=10.0), "follow_redirects": False}
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    try:
        with httpx.Client(**kwargs) as http:
            resp = http.request(method, f"https://{HOST}{path}", params=params, json=json,
                                headers={"xi-api-key": k, "accept": "audio/mpeg" if audio else "application/json"})
    except httpx.TimeoutException:
        raise TTSError(f"ElevenLabs didn't answer within {TIMEOUT_S:.0f} seconds") from None
    except httpx.HTTPError as exc:
        raise TTSError(f"couldn't reach ElevenLabs ({type(exc).__name__})") from None
    if resp.url.host != HOST:
        raise TTSError("ElevenLabs answered from an unexpected host; ignored")
    if 300 <= resp.status_code < 400:
        raise TTSError(f"ElevenLabs tried to redirect (HTTP {resp.status_code}); redirects are refused")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
            reason = clean(detail.get("message", "") if isinstance(detail, dict) else detail or "", (k,))
        except (ValueError, AttributeError):
            reason = ""
        raise TTSError(f"ElevenLabs answered HTTP {resp.status_code}{HINTS.get(resp.status_code, '')}"
                       + (f": {reason}" if reason else ""))
    if len(resp.content) > MAX_ANSWER_BYTES:
        raise TTSError("ElevenLabs' answer is too big; refused")
    if audio:
        return resp.content, str(resp.headers.get("request-id") or "")[:80] or None
    try:
        body = resp.json()
    except ValueError:
        raise TTSError("ElevenLabs sent an answer that isn't JSON") from None
    if not isinstance(body, dict):
        raise TTSError("ElevenLabs sent an answer of an unexpected shape")
    return body


def voices(search: str = "") -> list[dict]:
    """The account's voices (its own and the ones it added) as {voice_id, name, gender, accent, age, description,
    use_case, category, preview_url, labels}, from ElevenLabs at most once an hour per search."""
    search = (search or "").strip()[:100]
    now = time.time()
    hit = _voices_cache.get(search.lower())
    if hit and now - hit[0] < VOICES_TTL_S:
        return hit[1]
    out, token = [], None
    for _ in range(MAX_VOICE_PAGES):
        params = {"page_size": 100, **({"search": search} if search else {}), **({"next_page_token": token} if token else {})}
        body = request("GET", "/v2/voices", params=params)
        for v in body.get("voices") or []:
            if not isinstance(v, dict) or not VOICE_ID_RE.match(str(v.get("voice_id") or "")):
                continue
            labels = {str(a)[:40]: str(b)[:80] for a, b in (v.get("labels") or {}).items()} \
                if isinstance(v.get("labels"), dict) else {}
            out.append({"voice_id": v["voice_id"], "name": str(v.get("name") or "")[:80],
                        "gender": labels.get("gender"), "accent": labels.get("accent"), "age": labels.get("age"),
                        "use_case": labels.get("use_case"), "description": str(v.get("description") or "")[:300] or None,
                        "category": v.get("category"), "preview_url": v.get("preview_url"), "labels": labels})
        token = body.get("next_page_token")
        if not body.get("has_more") or not token:
            break
    out.sort(key=lambda v: v["name"].lower())
    _voices_cache[search.lower()] = (now, out)
    return out


def subscription() -> dict:
    """This billing period's characters from ElevenLabs itself: {tier, status, characters, limit, left, resets}."""
    body = request("GET", "/v1/user/subscription")
    used, limit = int(body.get("character_count") or 0), int(body.get("character_limit") or 0)
    reset = body.get("next_character_count_reset_unix")
    return {"tier": body.get("tier"), "status": body.get("status"), "characters": used, "limit": limit,
            "left": max(0, limit - used),
            "resets": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(reset))) if reset else None,
            "overage_possible": bool(body.get("can_extend_character_limit"))}


def synthesize(text: str, voice_id: str, model: str = "", stability: float | None = None,
               similarity_boost: float | None = None, speed: float | None = None, *, style: float | None = None,
               use_speaker_boost: bool | None = None, seed: int | None = None, previous_text: str = "",
               next_text: str = "", previous_request_ids: list | None = None, next_request_ids: list | None = None,
               output_format: str = "") -> tuple[bytes, dict]:
    """One piece as MP3 bytes, refused with TTS_CAP_REACHED when it would go past the plan's characters. The
    continuity fields (seed, previous/next text and request ids) are ElevenLabs' own, passed through as given."""
    global _reserved
    chars = len(text)
    if chars > MAX_CHARS:
        raise TTSError(f"text is {chars:,} characters; ElevenLabs pieces here are at most {MAX_CHARS:,}: cut it at "
                       "sentence ends")
    if not VOICE_ID_RE.match(str(voice_id or "")):
        raise TTSError("voice_id must be an ElevenLabs voice id from tts.voices(provider=\"elevenlabs\")")
    model = (model or DEFAULT_MODEL).strip()
    if not MODEL_RE.match(model):
        raise TTSError("model must be an ElevenLabs model id like eleven_multilingual_v2")
    fmt = (output_format or OUTPUT).strip()
    if fmt not in OUTPUTS:
        raise TTSError(f"output_format is one of {list(OUTPUTS)} (MP3 only: tts.fetch hands out MP3s)")
    extra: dict = {}
    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4_294_967_295:
            raise TTSError("seed is a whole number from 0 to 4294967295")
        extra["seed"] = seed
    for name, value in (("previous_text", previous_text), ("next_text", next_text)):
        if value:
            if not isinstance(value, str) or len(value) > MAX_CONTEXT_CHARS:
                raise TTSError(f"{name} is text of at most {MAX_CONTEXT_CHARS} characters")
            extra[name] = value
    for name, value in (("previous_request_ids", previous_request_ids), ("next_request_ids", next_request_ids)):
        if value:
            if not isinstance(value, list) or len(value) > 3 or not all(
                    isinstance(i, str) and REQUEST_ID_RE.match(i) for i in value):
                raise TTSError(f"{name} is a list of up to 3 request ids that tts.synthesize returned")
            extra[name] = value
    if use_speaker_boost is not None and not isinstance(use_speaker_boost, bool):
        raise TTSError("use_speaker_boost is true or false")
    settings = {} if use_speaker_boost is None else {"use_speaker_boost": use_speaker_boost}
    for name, value, lo, hi in (("stability", stability, 0.0, 1.0), ("similarity_boost", similarity_boost, 0.0, 1.0),
                                ("style", style, 0.0, 1.0), ("speed", speed, 0.7, 1.2)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= float(value) <= hi:
            raise TTSError(f"{name} is a number from {lo:g} to {hi:g}")
        settings[name] = float(value)
    with _lock:
        sub = subscription()
        if chars > sub["left"] - _reserved:
            raise TTSError(f"{CAP_REACHED}: this piece is {chars:,} characters and the ElevenLabs plan has "
                           f"{max(0, sub['left'] - _reserved):,} left this period (resets {sub['resets'] or 'unknown'}); "
                           "nothing was sent, so nothing was charged")
        _reserved += chars
    try:
        body = {"text": text, "model_id": model, **({"voice_settings": settings} if settings else {}), **extra}
        audio, request_id = request("POST", f"/v1/text-to-speech/{voice_id}", params={"output_format": fmt},
                                    json=body, audio=True)
    finally:
        with _lock:
            _reserved -= chars
    return audio, {"request_id": request_id, "output_format": fmt, "model": model, "left_before": sub["left"], "limit": sub["limit"], "resets": sub["resets"]}
