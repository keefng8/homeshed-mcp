"""Short-lived staging of media in a private Cloudflare R2 bucket (S3 API, SigV4), so a social network can fetch it.

Threads (and later Instagram) only take media from a public URL. The bucket stays private: each file gets a presigned
GET link that works for about an hour, and the caller deletes the object once the post is published. Cloudflare's
docs: presigned URLs work only on the S3 API domain (https://<account>.r2.cloudflarestorage.com), region "auto".

Vault entries (names only; values never leave the vault): R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_CLOUDFLARE_S3
(the S3 endpoint). The bucket is R2_BUCKET (default homeshed-studio). A presigned link is a bearer credential until it
expires: never log or return it beyond the call that needs it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import re
from urllib.parse import quote, urlsplit

import httpx

from tools.commerce import _http as h

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/._-]{0,200}$")
REGION, SERVICE = "auto", "s3"


class R2Error(h.CommerceError):
    """R2 isn't set up, or refused the upload."""


def _config() -> tuple[str, str, str, str]:
    kid, sec, endpoint = h.secret("R2_ACCESS_KEY_ID"), h.secret("R2_SECRET_ACCESS_KEY"), h.secret("R2_CLOUDFLARE_S3")
    missing = [n for n, v in (("R2_ACCESS_KEY_ID", kid), ("R2_SECRET_ACCESS_KEY", sec), ("R2_CLOUDFLARE_S3", endpoint))
               if not v]
    if missing:
        raise h.missing(missing, "Media staging (Cloudflare R2)")
    host = urlsplit(endpoint).hostname or ""
    if not host.endswith(".r2.cloudflarestorage.com"):
        raise R2Error("R2_CLOUDFLARE_S3 must be the bucket's S3 address (https://<account>.r2.cloudflarestorage.com)")
    return kid, sec, host, (os.environ.get("R2_BUCKET") or "homeshed-studio").strip()


def _sign_key(secret: str, day: str) -> bytes:
    k = hmac.new(("AWS4" + secret).encode(), day.encode(), hashlib.sha256).digest()
    for part in (REGION, SERVICE, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return k


def _path(bucket: str, key: str) -> str:
    if not KEY_RE.match(key) or ".." in key:
        raise R2Error("bad object key")
    return "/" + quote(bucket, safe="") + "/" + quote(key, safe="/-_.~")


def _q(params: dict) -> str:
    return "&".join(f"{quote(k, safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(params.items()))


def _signature(sec: str, now: dt.datetime, canonical: str) -> tuple[str, str]:
    day, stamp = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
    scope = f"{day}/{REGION}/{SERVICE}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    return hmac.new(_sign_key(sec, day), to_sign.encode(), hashlib.sha256).hexdigest(), scope


def _request(method: str, key: str, body: bytes = b"", content_type: str = "") -> httpx.Response:
    kid, sec, host, bucket = _config()
    now = dt.datetime.now(dt.timezone.utc)
    stamp, payload = now.strftime("%Y%m%dT%H%M%SZ"), hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload, "x-amz-date": stamp,
               **({"content-type": content_type} if content_type else {})}
    names = sorted(headers)
    canonical = "\n".join([method, _path(bucket, key), "", *(f"{n}:{headers[n]}" for n in names), "",
                           ";".join(names), payload])
    sig, scope = _signature(sec, now, canonical)
    headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={kid}/{scope}, SignedHeaders={';'.join(names)}, "
                                f"Signature={sig}")
    try:
        # a minute, plus time for big bodies (a video): about 2 s a megabyte on a slow upload
        return httpx.request(method, f"https://{host}{_path(bucket, key)}", headers=headers, content=body,
                             timeout=60 + 2 * len(body) / 2**20)
    except httpx.HTTPError as exc:
        raise R2Error(f"R2 didn't answer ({type(exc).__name__})") from None


def put(key: str, data: bytes, content_type: str) -> None:
    r = _request("PUT", key, data, content_type)
    if r.status_code != 200:
        raise R2Error(f"R2 refused the upload (HTTP {r.status_code})")


def delete(key: str) -> bool:
    """Best effort: True when gone (R2 answers 204 for a missing key too)."""
    try:
        return _request("DELETE", key).status_code in (200, 204)
    except R2Error:
        return False


def presign_get(key: str, expires_s: int = 3600) -> str:
    """A GET link for one object that works for expires_s seconds (1-7 days max per S3; we use about an hour)."""
    kid, sec, host, bucket = _config()
    now = dt.datetime.now(dt.timezone.utc)
    scope_day = now.strftime("%Y%m%d")
    params = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256",
              "X-Amz-Credential": f"{kid}/{scope_day}/{REGION}/{SERVICE}/aws4_request",
              "X-Amz-Date": now.strftime("%Y%m%dT%H%M%SZ"), "X-Amz-Expires": str(max(60, min(expires_s, 604800))),
              "X-Amz-SignedHeaders": "host"}
    canonical = "\n".join(["GET", _path(bucket, key), _q(params), f"host:{host}", "", "host", "UNSIGNED-PAYLOAD"])
    sig, _ = _signature(sec, now, canonical)
    return f"https://{host}{_path(bucket, key)}?{_q(params)}&X-Amz-Signature={sig}"
