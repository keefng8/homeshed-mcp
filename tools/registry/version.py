"""registry.version: a package's latest released version and when it went up, from PyPI, npm or GitHub releases, in a
few hundred characters (R&D C35, 2026-10-14: three releases were checked by hand, and web.read returned 77k characters
for one PyPI version). The first step of a release check. See ../../capabilities/registry/version.md.

Read-only, no auth, host-locked: pypi.org, registry.npmjs.org and api.github.com only, with a User-Agent (GitHub
refuses requests without one), 10 s timeout and an 8 MB cap. GitHub without a token allows 60 requests an hour per
address: past that the answer says "rate-limited", not an error.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from registry import tool

USER_AGENT = "HomeShed-registry-version/1.0"
TIMEOUT_S = 10
MAX_BYTES = 8_000_000
KEEP = 3  # earlier versions listed after the latest
NAMES = {
    "pypi": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"),
    "npm": re.compile(r"^(@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]{0,213}$"),
    "github": re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$"),
}


class RegistryError(ValueError):
    """A version lookup that can't be answered, said plainly."""


def _get(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            body = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LookupError("not found") from None
        if exc.code in (403, 429) and (exc.headers.get("X-RateLimit-Remaining") == "0" or exc.code == 429):
            raise RegistryError("rate-limited: GitHub allows 60 lookups an hour without a token; try again later") \
                from None
        raise RegistryError(f"the registry answered HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RegistryError(f"the registry couldn't be reached: {getattr(exc, 'reason', exc)}") from None
    if len(body) > MAX_BYTES:
        raise RegistryError("the registry's answer was too large")
    return json.loads(body)


def _when(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _utc(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else None


def _pypi(name: str) -> dict:
    d = _get(f"https://pypi.org/pypi/{name}/json")
    latest = (d.get("info") or {}).get("version")
    rows = []
    for version, files in (d.get("releases") or {}).items():
        files = [f for f in files or [] if isinstance(f, dict)]
        if not files:
            continue  # a version number with no upload
        when = min((t for t in (_when(f.get("upload_time_iso_8601") or f.get("upload_time")) for f in files) if t),
                   default=None)
        rows.append({"version": version, "released": when, "yanked": all(f.get("yanked") for f in files)})
    rows.sort(key=lambda r: r["released"] or 0, reverse=True)  # by upload time: "0.10" sorts before "0.9" as text
    top = next((r for r in rows if r["version"] == latest), None)
    return {"latest": latest, "released": top and top["released"], "url": f"https://pypi.org/project/{name}/{latest}/",
            "rows": rows}


def _npm(name: str) -> dict:
    d = _get(f"https://registry.npmjs.org/{name.replace('/', '%2F')}")
    latest = (d.get("dist-tags") or {}).get("latest")
    times = d.get("time") or {}
    rows = [{"version": v, "released": _when(t)} for v, t in times.items() if v not in ("created", "modified")]
    rows.sort(key=lambda r: r["released"] or 0, reverse=True)
    return {"latest": latest, "released": _when(times.get(latest)), "url": f"https://www.npmjs.com/package/{name}",
            "rows": rows}


def _github(name: str) -> dict:
    d = _get(f"https://api.github.com/repos/{name}/releases?per_page=10")
    releases = [r for r in d if isinstance(r, dict) and not r.get("draft") and not r.get("prerelease")] \
        if isinstance(d, list) else []
    if not releases:
        return {"latest": None, "released": None, "url": f"https://github.com/{name}/releases", "rows": []}
    rows = [{"version": r.get("tag_name"), "released": _when(r.get("published_at"))} for r in releases]
    rows.sort(key=lambda r: r["released"] or 0, reverse=True)
    return {"latest": rows[0]["version"], "released": rows[0]["released"], "url": releases[0].get("html_url"),
            "rows": rows}


@tool(name="version", category="registry", doc="registry/version.md")
def version(ecosystem: str, name: str) -> dict:
    """A package's latest released version and when it was released, from PyPI, npm or GitHub releases: a few hundred
    characters, for checking that a release really went out. Read-only, no keys.

    Args:
        ecosystem: "pypi", "npm" or "github".
        name: the package name (pypi, npm), or "owner/repo" (github; its latest release, not drafts or prereleases).

    Returns:
        {"ecosystem", "name", "latest", "released": unix seconds, "released_utc", "url",
         "previous": [{"version", "released_utc", "yanked"?}] (up to 3, newest first)}.
        "latest" is null with "note": "no releases" when there are none; a missing package says "not found".
    """
    eco = str(ecosystem or "").strip().lower()
    name = str(name or "").strip()
    if eco not in NAMES:
        raise RegistryError("ecosystem: pypi, npm or github")
    if not NAMES[eco].match(name):
        raise RegistryError("name: a package name (pypi, npm) or owner/repo (github)")
    try:
        got = {"pypi": _pypi, "npm": _npm, "github": _github}[eco](name)
    except LookupError:
        return {"ecosystem": eco, "name": name, "latest": None, "note": "not found" if eco != "github" else
                "no releases (or no such repository)"}
    out = {"ecosystem": eco, "name": name, "latest": got["latest"], "released": got["released"],
           "released_utc": _utc(got["released"]), "url": got["url"],
           "previous": [{"version": r["version"], "released_utc": _utc(r["released"]),
                         **({"yanked": True} if r.get("yanked") else {})}
                        for r in got["rows"] if r["version"] != got["latest"]][:KEEP]}
    if got["latest"] is None:
        out["note"] = "no releases"
    return out
