"""One host-locked HTTPS client for the shop proxy tools. See ../../capabilities/etsy/read.md.

Every request goes to a fixed host per service (api.etsy.com, api.printify.com), over https, with redirects refused,
a timeout, and a size cap. Errors carry the HTTP status and a short reason with every URL and every secret used in
the request scrubbed out; never a header, a token or a URL with a query.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

HOSTS = {"etsy": "api.etsy.com", "printify": "api.printify.com", "threads": "graph.threads.net",
         "instagram": "graph.instagram.com", "instagram-auth": "api.instagram.com",  # Instagram API with Instagram Login
         "pinterest": "api.pinterest.com", "pinterest-sandbox": "api-sandbox.pinterest.com",
         "bing": "ssl.bing.com"}  # Bing Webmaster Tools' keyword stats (signal.keywords)
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


ETSY_QUOTA: dict = {}  # the last Etsy answer's own limit headers (2026-10-21: a 429 "daily rate limit")


def _note_quota(resp: httpx.Response) -> None:
    """Etsy sends x-limit-per-day / x-remaining-today (and per-second) on every answer, refusals included."""
    got = {}
    for key, head in (("limit_per_day", "x-limit-per-day"), ("remaining_today", "x-remaining-today"),
                      ("limit_per_second", "x-limit-per-second"), ("remaining_this_second", "x-remaining-this-second")):
        v = resp.headers.get(head)
        if v is not None and str(v).strip().isdigit():
            got[key] = int(v)
    if got:
        ETSY_QUOTA.update(got, seen_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          last_status=resp.status_code)


def etsy_quota() -> dict | None:
    """What Etsy last said about this app's quota, or None before any call since the server started."""
    return dict(ETSY_QUOTA) or None


# Real Etsy requests per UTC day, client and tool, refusals included (2026-10-23: 3,600 of the 5,000 a day were gone
# with no way to say who used them). One tool call can make several requests (shop.get makes 4), so this counts what
# Etsy counts, not tool calls. Kept for KEEP_DAYS.
_REQ_LOCK = threading.Lock()
KEEP_DAYS = 14
# Background work has no tool of its own (2026-10-23: 73 of 342 requests were "?"): the approvals queue, signals and
# the token refresh name themselves. (tag, force): force wins over the tool; otherwise the tag is only used outside one.
_COUNT_AS: contextvars.ContextVar[tuple[str, bool] | None] = contextvars.ContextVar("etsy_count_as", default=None)


@contextlib.contextmanager
def counted_as(tag: str, force: bool = False):
    token = _COUNT_AS.set((tag, force))
    try:
        yield
    finally:
        _COUNT_AS.reset(token)


def _requests_file() -> Path:
    from paths import data_path
    return Path(os.environ.get("ETSY_REQUESTS_FILE") or data_path("usage/etsy_requests.json"))


def _read_requests() -> dict:
    try:
        d = json.loads(_requests_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _count_request(status: int) -> None:
    import clients
    client = clients.current_client.get() or "owner"
    tag, force = _COUNT_AS.get() or ("", False)
    tool = (force and tag) or clients.current_tool.get() or tag or "?"
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _REQ_LOCK:
        d = _read_requests()
        row = d.setdefault(day, {}).setdefault(client, {}).setdefault(tool, {"requests": 0, "refused_429": 0})
        row["requests"] += 1
        row["refused_429"] += status == 429
        for old in sorted(d)[:-KEEP_DAYS]:
            del d[old]
        path = _requests_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass  # counting never stops the call


def etsy_requests(day: str = "") -> dict:
    """{client: {tool: {"requests", "refused_429"}}} for one UTC day (default today)."""
    with _REQ_LOCK:
        return _read_requests().get(day or time.strftime("%Y-%m-%d", time.gmtime()), {})


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
    if service == "etsy":
        _note_quota(resp)
        _count_request(resp.status_code)
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
                            "(Control Panel > Settings > Shops and social)")


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
