"""web.search. See ../../capabilities/web/search.md.

Searches the web through a self-hosted SearXNG (a free metasearch engine: no API key, no running cost) set by
SEARXNG_URL. Returns links and short snippets only; reading a page is web.read's job, so one search plus a few reads
covers a research question without paying for whole pages that weren't wanted.
"""
from __future__ import annotations

import os
import re

import httpx

from registry import tool

MAX_RESULTS = 10
MAX_QUERY = 300
SNIPPET_CHARS = 300
TIME_RANGES = ("", "day", "week", "month", "year")
_SPACE = re.compile(r"\s+")


class WebSearchError(RuntimeError):
    """A bad query, or the search engine isn't set up or didn't answer. Never includes the engine's address."""


def _base() -> str:
    url = (os.environ.get("SEARXNG_URL") or "").strip().rstrip("/")
    if not url:
        raise WebSearchError("web.search isn't set up here: it needs a SearXNG search engine (SEARXNG_URL)")
    return url


def _clean(text, limit: int) -> str:
    text = _SPACE.sub(" ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@tool(name="search", category="web", doc="web/search.md")
def search(query: str, max_results: int = 5, time_range: str = "", language: str = "en") -> dict:
    """Search the web; returns titles, links and short snippets (read a page with web.read).

    Args:
        query: what to search for (1-300 characters).
        max_results: 1-10 (default 5).
        time_range: "" (any time), "day", "week", "month" or "year".
        language: a language code such as "en" or "en-GB" (default "en"), or "all".

    Returns:
        {"query", "results": [{"title", "url", "snippet", "engine"}], "suggestions": [...]} with no duplicate links.

    Raises:
        WebSearchError: an empty or too-long query, a bad option, or the search engine unset or unreachable.
    """
    query = _SPACE.sub(" ", query or "").strip()
    if not query:
        raise WebSearchError("query must be non-empty")
    if len(query) > MAX_QUERY:
        raise WebSearchError(f"query must be at most {MAX_QUERY} characters")
    if not isinstance(max_results, int) or not 1 <= max_results <= MAX_RESULTS:
        raise WebSearchError(f"max_results must be 1-{MAX_RESULTS}")
    if time_range not in TIME_RANGES:
        raise WebSearchError('time_range is "", "day", "week", "month" or "year"')
    if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z]{2})?|all", language or ""):
        raise WebSearchError('language is a code such as "en" or "en-GB", or "all"')
    params = {"q": query, "format": "json", "language": language, **({"time_range": time_range} if time_range else {})}
    try:
        r = httpx.get(f"{_base()}/search", params=params, timeout=20)
    except httpx.TimeoutException:
        raise WebSearchError("the search engine timed out; try again or narrow the query") from None
    except httpx.HTTPError:
        raise WebSearchError("the search engine is unavailable") from None
    if r.status_code != 200:
        raise WebSearchError(f"the search engine answered HTTP {r.status_code}")
    try:
        data = r.json()
        raw = data.get("results") or []
    except (ValueError, AttributeError):
        raise WebSearchError("the search engine's answer wasn't readable") from None
    results, seen = [], set()
    for item in raw:
        url = str((item or {}).get("url") or "")
        if not url.startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        results.append({"title": _clean(item.get("title"), 200), "url": url,
                        "snippet": _clean(item.get("content"), SNIPPET_CHARS), "engine": str(item.get("engine") or "")})
        if len(results) >= max_results:
            break
    return {"query": query, "results": results,
            "suggestions": [_clean(s, 100) for s in (data.get("suggestions") or [])[:5]]}
