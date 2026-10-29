"""The one way the tts.* tools talk to Google: a fixed host, https only, redirects refused, a timeout and a size cap.

Facts checked 2026-10-28 on Google's own pages (the URLs are in ../../capabilities/tts/synthesize.md):
- a request may carry at most 5,000 bytes of input (docs.cloud.google.com/text-to-speech/quotas);
- Chirp 3 HD can answer in MP3 and takes speaking_rate 0.25 to 2.0 (docs.cloud.google.com/text-to-speech/docs/chirp3-hd);
- the key goes in the x-goog-api-key header, never the address (docs.cloud.google.com/docs/authentication/api-keys-use).
Sign-in: a plain API key is refused ("API keys are not supported by this API. Expected OAuth2 access token", the first
real call, 2026-10-28). So the tools sign in as a service account: its JSON key file sits in the vault as
GOOGLE_TTS_SERVICE_ACCOUNT; we sign a short JWT with its private key (RS256) and swap it at Google's token address for
an access token, kept until a minute before it expires. Our own code, from Google's documented OAuth 2.0
service-account flow; no Google SDK. An API key (GOOGLE_TTS_API_KEY) is still used if there's no service account, for
projects where Google accepts one.
"""
from __future__ import annotations

import base64
import json as _json
import re
import threading
import time

import httpx

from tools.commerce._http import clean, secret

HOST = "texttospeech.googleapis.com"
KEY_NAME = "GOOGLE_TTS_API_KEY"
ACCOUNT_NAME = "GOOGLE_TTS_SERVICE_ACCOUNT"
TOKEN_HOST = "oauth2.googleapis.com"
TOKEN_URL = f"https://{TOKEN_HOST}/token"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
TOKEN_EARLY_S = 60  # get a new access token this long before the old one runs out
MAX_INPUT_BYTES = 5000
MAX_ANSWER_BYTES = 30 * 1024 * 1024  # 5,000 bytes of text is a few minutes of MP3: a few MB at most
TIMEOUT_S = 120.0  # a long piece takes Google a while to speak
VOICES_TTL_S = 24 * 3600
# en-GB-Chirp3-HD-Kore: a language, a region, the model and the voice's own name (Google's naming on the Chirp 3 HD page)
VOICE_RE = re.compile(r"^([a-z]{2,3}-[A-Za-z0-9]{2,4})-Chirp3-HD-[A-Za-z]{2,30}$", re.IGNORECASE)
LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,4})?$", re.IGNORECASE)
GUIDE = "the setup guide: Control Panel > Guides > Google text to speech"
HINTS = {400: " (Google refused the request)", 401: " (the key was refused)",
         403: " (the key can't use Text-to-Speech: check the API is enabled and the key's restriction)",
         429: " (over Google's per-minute limit: wait a minute)"}

# Tests set this to an httpx.MockTransport so no request ever leaves the process.
_TRANSPORT: httpx.BaseTransport | None = None
_voices_cache: dict[str, tuple[float, list[dict]]] = {}  # language ("" = all) -> (when fetched, voices)


class TTSError(RuntimeError):
    """Safe to show: never a key or an address."""


_token: dict = {"value": "", "until": 0.0, "account": ""}
_token_lock = threading.Lock()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _account() -> dict | None:
    """The service account's key file from the vault, checked; None when there isn't one."""
    raw = secret(ACCOUNT_NAME)
    if not raw:
        return None
    try:
        acct = _json.loads(raw)
    except ValueError:
        raise TTSError(f"{ACCOUNT_NAME} isn't a JSON key file: paste the whole file Google downloaded ({GUIDE})") from None
    if not isinstance(acct, dict) or acct.get("type") != "service_account" or not acct.get("client_email") \
            or "PRIVATE KEY" not in str(acct.get("private_key") or ""):
        raise TTSError(f"{ACCOUNT_NAME} isn't a service account key file ({GUIDE})")
    if acct.get("token_uri", TOKEN_URL) != TOKEN_URL:  # never sign in anywhere but Google's own token address
        raise TTSError(f"{ACCOUNT_NAME} names a token address other than Google's; refused")
    return acct


def _signed_jwt(acct: dict, now: float) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    try:
        pkey = serialization.load_pem_private_key(str(acct["private_key"]).encode("utf-8"), password=None)
    except (ValueError, TypeError):
        raise TTSError(f"the private key in {ACCOUNT_NAME} can't be read: download a new key file ({GUIDE})") from None
    head = _b64url(_json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(_json.dumps({"iss": acct["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                                  "iat": int(now), "exp": int(now) + 3600}).encode())
    signature = pkey.sign(f"{head}.{claims}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{claims}.{_b64url(signature)}"


def _access_token(acct: dict, http: httpx.Client) -> str:
    """A cached access token for the service account, or a fresh one from Google's token address."""
    now = time.time()
    with _token_lock:
        if _token["value"] and _token["account"] == acct["client_email"] and now < _token["until"] - TOKEN_EARLY_S:
            return _token["value"]
        try:
            resp = http.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                              "assertion": _signed_jwt(acct, now)})
        except httpx.HTTPError as exc:
            raise TTSError(f"couldn't reach Google's sign-in ({type(exc).__name__})") from None
        if resp.url.host != TOKEN_HOST or resp.status_code != 200:
            try:
                reason = str((resp.json() or {}).get("error_description") or "")[:160]
            except ValueError:
                reason = ""
            raise TTSError(f"Google's sign-in refused the service account (HTTP {resp.status_code})"
                           + (f": {reason}" if reason else "") + f" ({GUIDE})")
        body = resp.json()
        value = str(body.get("access_token") or "")
        if not value:
            raise TTSError("Google's sign-in answered without an access token")
        _token.update(value=value, until=now + float(body.get("expires_in") or 3600), account=acct["client_email"])
        return value


def ready() -> None:
    """Raise plainly when no Google sign-in is set up (or the key file is bad), before anything is counted or sent."""
    if _account() is None and not secret(KEY_NAME):
        raise TTSError(f"Google text to speech isn't set up: add {ACCOUNT_NAME} (the service account's key file) "
                       f"under Settings > Credentials ({GUIDE})")


def _auth(http: httpx.Client) -> tuple[dict, tuple[str, ...]]:
    """The sign-in header, and the secrets to keep out of any error text."""
    acct = _account()
    if acct is not None:
        token = _access_token(acct, http)
        return {"Authorization": f"Bearer {token}"}, (token,)
    k = secret(KEY_NAME)
    if not k:
        raise TTSError(f"Google text to speech isn't set up: add {ACCOUNT_NAME} (the service account's key file) "
                       f"under Settings > Credentials ({GUIDE})")
    return {"x-goog-api-key": k}, (k,)


def request(method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> dict:
    """One call to Google's text-to-speech host; its JSON answer."""
    kwargs: dict = {"timeout": httpx.Timeout(TIMEOUT_S, connect=10.0), "follow_redirects": False}
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    try:
        with httpx.Client(**kwargs) as http:
            headers, hidden = _auth(http)
            resp = http.request(method, f"https://{HOST}{path}", params=params, json=json, headers=headers)
    except httpx.TimeoutException:
        raise TTSError(f"Google didn't answer within {TIMEOUT_S:.0f} seconds") from None
    except httpx.HTTPError as exc:
        raise TTSError(f"couldn't reach Google ({type(exc).__name__})") from None
    if resp.url.host != HOST:  # belt and braces: the client never follows redirects
        raise TTSError("Google answered from an unexpected host; ignored")
    if 300 <= resp.status_code < 400:
        raise TTSError(f"Google tried to redirect (HTTP {resp.status_code}); redirects are refused")
    if resp.status_code >= 400:
        if resp.status_code == 401:
            _token.update(value="", until=0.0)  # a refused access token isn't reused
        try:
            reason = clean((resp.json().get("error") or {}).get("message", ""), hidden)
        except (ValueError, AttributeError):
            reason = ""
        raise TTSError(f"Google answered HTTP {resp.status_code}{HINTS.get(resp.status_code, '')}"
                       + (f": {reason}" if reason else ""))
    if len(resp.content) > MAX_ANSWER_BYTES:
        raise TTSError("Google's answer is too big; refused")
    try:
        body = resp.json()
    except ValueError:
        raise TTSError("Google sent an answer that isn't JSON") from None
    if not isinstance(body, dict):
        raise TTSError("Google sent an answer of an unexpected shape")
    return body


def voices(language: str = "") -> list[dict]:
    """Chirp 3 HD voices as {name, gender, language}, from Google at most once a day per language."""
    language = (language or "").strip()
    if language and not LANG_RE.match(language):
        raise TTSError("language is a code like en-GB or en")
    now = time.time()
    hit = _voices_cache.get(language.lower())
    if hit and now - hit[0] < VOICES_TTL_S:
        return hit[1]
    body = request("GET", "/v1/voices", params={"languageCode": language} if language else None)
    out = []
    for v in body.get("voices") or []:
        name = str(v.get("name") or "") if isinstance(v, dict) else ""
        if VOICE_RE.match(name):
            langs = v.get("languageCodes") or []
            out.append({"name": name, "gender": str(v.get("ssmlGender") or "").lower() or None,
                        "language": langs[0] if langs else VOICE_RE.match(name).group(1)})
    out.sort(key=lambda v: (v["language"], v["name"]))
    _voices_cache[language.lower()] = (now, out)
    return out
