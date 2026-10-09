"""web.read. See ../../capabilities/web/read.md.

Ported from reference-repos/Agent-Reach's channels/web.py + utils/url.py (MIT), per
.claude/external-audit-agent-reach.md's ADAPT verdict — these two files were the only directly
reusable pieces of that repo. SSRF-hardening in _normalize_public_http_url kept close to the
original logic (don't weaken it while porting); everything else (antibot detection, size cap,
Jina Reader call) re-expressed in this project's own error-handling style.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.request
from urllib.parse import urlsplit

from registry import tool

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_ANTIBOT_SCAN_BYTES = 4096

_BLOCKED_HOSTS = {
    "home.arpa", "instance-data", "internal", "ip6-localhost", "ip6-loopback",
    "lan", "local", "localdomain", "localhost", "metadata.google.internal",
}
_BLOCKED_SUFFIXES = (
    ".home.arpa", ".internal", ".lan", ".local", ".localdomain", ".localhost",
)


class WebReadError(RuntimeError):
    """Bad URL, unreachable page, response too large, or an anti-bot challenge page."""


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse canonical and legacy IPv4 literal spellings without a DNS lookup."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def _normalize_public_http_url(url: str) -> str:
    """Reject anything that isn't clearly a public HTTP(S) URL — no localhost/private-IP/
    metadata-endpoint targets, no userinfo tricks, no control characters. SSRF defense; this
    function is the reason web.read is safe to expose at all, don't loosen it casually."""
    candidate = str(url or "").strip()
    if (
        not candidate
        or "\\" in candidate
        or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate)
    ):
        raise WebReadError("only public HTTP(S) URLs are allowed")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        _ = parsed.port  # accessing it rejects malformed/out-of-range authorities
    except (TypeError, ValueError):
        raise WebReadError("only public HTTP(S) URLs are allowed") from None

    literal = _literal_ip(host)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or "%" in host
        or host in _BLOCKED_HOSTS
        or host.endswith(_BLOCKED_SUFFIXES)
        or ("." not in host and literal is None)
        or (literal is not None and not literal.is_global)
    ):
        raise WebReadError("only public HTTP(S) URLs are allowed")

    return parsed.geturl()


def _is_antibot_page(body: bytes) -> bool:
    """Recognize high-confidence Jina/Cloudflare challenge responses so a captcha page never
    silently gets returned as if it were real content."""
    sample = body[:_ANTIBOT_SCAN_BYTES].decode("utf-8", errors="ignore").casefold()
    jina_captcha = "warning:" in sample and "requiring captcha" in sample
    challenge_structure = any(
        marker in sample
        for marker in (
            "title: just a moment...",
            "## performing security verification",
            "title: attention required! | cloudflare",
        )
    )
    cloudflare_block = "title: attention required! | cloudflare" in sample and (
        "ray id" in sample or "/cdn-cgi/challenge-platform/" in sample
    )
    return (jina_captcha and challenge_structure) or cloudflare_block


@tool(name="read", category="web", doc="web/read.md")
def read(url: str) -> dict:
    """Fetch a public webpage and return it as Markdown, via Jina Reader (r.jina.ai) — zero
    API key, zero account, no external dependency beyond stdlib. Ported from Agent-Reach's
    WebChannel, see this file's module docstring.

    Args:
        url: the page to read. Scheme optional (defaults to https). Must resolve to a public
            HTTP(S) address — localhost, private/link-local IPs, and cloud metadata endpoints
            are rejected before any request is made.

    Returns:
        {"url": str, "content": str} — content is the page as Markdown, as returned by Jina
        Reader (not this project's own rendering).

    Raises:
        WebReadError: not a public HTTP(S) URL, unreachable, response over 5MB, or Jina Reader
            returned an anti-bot challenge page instead of real content.
    """
    normalized = _normalize_public_http_url(url)
    jina_url = f"https://r.jina.ai/{normalized}"
    req = urllib.request.Request(jina_url, headers={"User-Agent": _UA, "Accept": "text/plain"})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.URLError as e:
        raise WebReadError(f"could not fetch {normalized}: {e.reason}") from None

    if len(body) > _MAX_RESPONSE_BYTES:
        raise WebReadError(f"response exceeds {_MAX_RESPONSE_BYTES} byte limit")
    if _is_antibot_page(body):
        raise WebReadError(
            "Jina Reader returned an anti-bot challenge page, not real content — "
            "try a site-specific tool or a real browser instead"
        )

    return {"url": normalized, "content": body.decode("utf-8", errors="replace")}
