"""One host-locked HTTPS client for the shop proxy tools. See ../../capabilities/etsy/read.md.

Every request goes to a fixed host per service (api.etsy.com, api.printify.com), over https, with redirects refused,
a timeout, and a size cap. Errors carry the HTTP status and a short reason with every URL and every secret used in
the request scrubbed out; never a header, a token or a URL with a query.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

HOSTS = {"etsy": "api.etsy.com", "printify": "api.printify.com", "threads": "graph.threads.net"}
TIMEOUT_S = 15.0
TIMEOUT = httpx.Timeout(TIMEOUT_S, connect=5.0)
MAX_BYTES = 8 * 1024 * 1024
_PATH = re.compile(r"^/[A-Za-z0-9/_.\-]+$")
_URL = re.compile(r"\b(?:https?|data):\S+", re.IGNORECASE)
HINTS = {401: " (the key or token was refused)", 403: " (the key lacks access: check the app's scopes)",
         404: " (not found)", 429: " (rate limited: try again in a minute)"}

# Tests set this to an httpx.MockTransport so no request ever leaves the process.
_TRANSPORT: httpx.BaseTransport | None = None


class CommerceError(RuntimeError):
    """Safe to show: no key, no token, no URL."""


def clean(text: Any, secrets: tuple[str, ...] = ()) -> str:
    out = _URL.sub("<url>", str(text or ""))
    for s in secrets:
        if s and len(s) >= 4:
            out = out.replace(s, "<secret>")
    return re.sub(r"\s+", " ", out).strip()[:200]


def _reason(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    for k in ("error_description", "error", "message", "detail", "errors"):
        v = body.get(k)
        if isinstance(v, dict):
            v = v.get("message") or v.get("reason") or ""
        if isinstance(v, list):
            v = "; ".join(str(x) for x in v[:3])
        if v:
            return str(v)
    return ""


def request(service: str, method: str, path: str, *, headers: dict, secrets: tuple[str, ...],
            params: dict | None = None, data: dict | None = None, json: dict | None = None,
            files: dict | None = None) -> httpx.Response:
    """One call to the service's own host. path must be a plain absolute path (no scheme, host, query or '..')."""
    host = HOSTS[service]
    if not _PATH.match(path) or ".." in path or "//" in path:
        raise CommerceError(f"refused an unexpected {service} path")
    name = service.capitalize()
    kwargs: dict = {"timeout": TIMEOUT, "follow_redirects": False}
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    try:
        with httpx.Client(**kwargs) as http:
            resp = http.request(method, f"https://{host}{path}", params=params, data=data, json=json, files=files,
                                headers=headers)
    except httpx.TimeoutException:
        raise CommerceError(f"{name} didn't answer within {TIMEOUT_S:.0f} seconds") from None
    except httpx.HTTPError as exc:
        raise CommerceError(f"couldn't reach {name} ({type(exc).__name__})") from None
    if resp.url.host != host:  # belt and braces: the client never follows redirects
        raise CommerceError(f"{name} answered from an unexpected host; ignored")
    if 300 <= resp.status_code < 400:
        raise CommerceError(f"{name} tried to redirect (HTTP {resp.status_code}); redirects are refused")
    if resp.status_code >= 400:
        reason = clean(_reason(resp), secrets)
        raise CommerceError(f"{name} answered HTTP {resp.status_code}{HINTS.get(resp.status_code, '')}"
                            + (f": {reason}" if reason else ""))
    if len(resp.content) > MAX_BYTES:
        raise CommerceError(f"{name}'s answer is over {MAX_BYTES // 2**20} MB; refused")
    return resp


def json_of(service: str, resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        raise CommerceError(f"{service.capitalize()} sent an answer that isn't JSON") from None


def secret(name: str) -> str | None:
    import vault  # stored credentials first, then the environment (vault.py)
    value = vault.secret(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def enabled(setting: str) -> bool:
    """The owner's live switch (runtime_settings); a store that can't be read means off."""
    try:
        import runtime_settings
        return runtime_settings.get(setting) is True
    except Exception:  # noqa: BLE001 - unreadable settings never switch a shop on
        return False


def require(setting: str, label: str) -> None:
    if not enabled(setting):
        raise CommerceError(f"{label} access is switched off: only the owner can switch it on "
                            "(Control Panel > Settings > Online shops)")


def missing(names: list[str], what: str) -> CommerceError:
    return CommerceError(f"{what} isn't configured: add {', '.join(names)} under the Control Panel's Keys and "
                         "passwords (names only are shown here; values never are)")


def trim(text: Any, n: int) -> str | None:
    if text is None:
        return None
    s = str(text)
    return s if len(s) <= n else s[: n - 1] + "…"


def money(m: Any) -> dict | None:
    """Etsy's {amount, divisor, currency_code} as {"amount": 12.5, "currency": "GBP"}."""
    if not isinstance(m, dict) or m.get("amount") is None:
        return None
    try:
        return {"amount": round(int(m["amount"]) / int(m.get("divisor") or 1), 2), "currency": m.get("currency_code")}
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def check_id(value: Any, what: str) -> str:
    s = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,20}", s):
        raise CommerceError(f"{what} must be a number")
    return s


def check_hex_id(value: Any, what: str) -> str:
    s = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9]{1,40}", s):
        raise CommerceError(f"{what} must be letters and digits only")
    return s


def clamp(n: Any, lo: int, hi: int, what: str) -> int:
    try:
        v = int(n)
    except (TypeError, ValueError):
        raise CommerceError(f"{what} must be a whole number") from None
    if not lo <= v <= hi:
        raise CommerceError(f"{what} must be {lo} to {hi}")
    return v
