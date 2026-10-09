"""apis.list / apis.find. See ../../capabilities/apis/list.md.

The shared API catalog (api_catalog.json): every API this platform uses or has evaluated, for any
session or person with MCP access, so nobody re-researches an API already tried. Read-only. Secrets never live in the file
(auth names the env var); the loader also refuses to serve anything that looks like a key.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from registry import tool

CATALOG_FILE = Path(os.environ.get("API_CATALOG_FILE", Path(__file__).resolve().parents[2] / "api_catalog.json"))
FIELDS = ("name", "category", "provider", "cost", "local", "status", "auth", "how_to_use", "notes", "tags")
# Belt and braces: if someone ever pastes a real key into the catalog, refuse to serve it.
_SECRET = re.compile(r"(sk-[A-Za-z0-9]{12,}|tk_[a-z0-9]{20,}|nvapi-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})")
_WORD = re.compile(r"[a-z0-9]+")


class CatalogError(RuntimeError):
    """The catalog can't be served (missing/malformed file, or it appears to contain a secret)."""


def _read(path: Path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8")
        entries = json.loads(raw)["apis"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CatalogError(f"API catalog unreadable ({type(exc).__name__})") from None
    if _SECRET.search(raw):
        raise CatalogError("API catalog appears to contain a secret value; refusing to serve it. "
                           "Store keys in env vars and name the var in 'auth'.")
    return entries


def _load() -> list[dict]:
    """The catalog, plus api_catalog.private.json when it's there (an install's own services; the public copy has
    none): a private entry replaces the public one with the same name, and the rest are added."""
    entries = _read(CATALOG_FILE)
    private = CATALOG_FILE.with_name("api_catalog.private.json")
    if private.exists():
        mine = {e.get("name"): e for e in _read(private)}
        entries = [mine.pop(e.get("name"), e) for e in entries] + list(mine.values())
    return [{k: e.get(k) for k in FIELDS} for e in entries]


@tool(name="list", category="apis", doc="apis/list.md")
def list_apis(category: str = "", cost: str = "", local: bool | None = None, status: str = "") -> dict:
    """List APIs in the shared catalog, optionally filtered. Use it to learn what's available
    before building something new, or to check whether an API is free, local, and live.

    Args:
        category: e.g. "llm", "search", "notify", "image", "classification" (empty = all).
        cost: "free", "free-tier", "paid", ... (substring match; empty = all).
        local: True = self-hosted only, False = external only, None = both.
        status: "live", "needs-setup", "candidate", "reference" (empty = all).

    Returns:
        {"count", "apis": [{name, category, provider, cost, local, status, auth, how_to_use, notes}]}.
        An unknown filter value simply returns no matches.
    """
    out = [e for e in _load()
           if (not category or (e["category"] or "").lower() == category.lower())
           and (not cost or cost.lower() in (e["cost"] or "").lower())
           and (local is None or bool(e["local"]) == local)
           and (not status or (e["status"] or "").lower() == status.lower())]
    return {"count": len(out), "apis": out}


@tool(name="find", category="apis", doc="apis/list.md")
def find_apis(query: str, k: int = 5) -> dict:
    """Find APIs by what you need, e.g. "text to speech", "free web search", "send a push".

    Args:
        query: plain words describing the need.
        k: max results, 1-25.

    Returns:
        {"results": [{...entry, "score"}]}, best first. Keyword overlap across name, category,
        how_to_use and notes; a name match ranks highest. Zero-score entries are omitted.
    """
    if not query or not query.strip():
        raise CatalogError("query must be non-empty")
    if not 1 <= k <= 25:
        raise CatalogError("k must be between 1 and 25")
    q = set(_WORD.findall(query.lower()))

    def hits(words: set[str]) -> int:
        # A query word counts if it equals, or is a prefix (3+ chars) of, a word in the entry:
        # "voice" finds "Voicebox". Found live 2026-09-28: whole-word matching missed it.
        return sum(1 for w in q if any(x == w or (len(w) >= 3 and x.startswith(w)) for x in words))

    scored = []
    for e in _load():
        name_words = set(_WORD.findall((e["name"] or "").lower()))
        tag_words = set(_WORD.findall(" ".join(e.get("tags") or []).lower()))
        body = set(_WORD.findall(" ".join(str(e[f] or "") for f in ("category", "provider", "how_to_use", "notes")).lower()))
        score = 3 * hits(name_words) + 2 * hits(tag_words) + hits(body)
        if query.strip().lower() == (e["name"] or "").lower():
            score += 100
        if score:
            scored.append({**e, "score": score})
    scored.sort(key=lambda x: -x["score"])
    return {"results": scored[:k]}
