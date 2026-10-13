"""repo.stats. See ../../capabilities/repo/stats.md.

How a public GitHub repository is doing (the owner, 2026-10-10: "can we get stats on my dash from my github link?"):
stars, forks, watchers and open issues, release downloads, PyPI downloads (pypistats.org, when the package names
the repo), and, with a GitHub token that can read the repo's traffic, views, clones and referrers.

GitHub keeps traffic for 14 days only, so each day's views and clones are kept here (HISTORY) and the totals count
from the first look on. Stars, forks and downloads are noted once a day too, for "+N this week". Answers are cached
CACHE_S. The token comes from the vault (TOKEN_NAMES, first one set); the tool never shows it.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from paths import data_path
from registry import tool

API = "https://api.github.com"
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
TOKEN_NAMES = ("GITHUB_STATS_TOKEN", "GITHUB_TOKEN", "GITHUB_SEARCH_TOKEN")
HISTORY = Path(data_path("usage/repo_stats.json"))
CACHE_S = 1800
KEEP_DAYS = 400
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


class StatsError(RuntimeError):
    """A malformed repo name, or GitHub didn't answer for it (missing, private without access, or down)."""


def _token() -> str | None:
    try:
        import vault
    except ImportError:
        return None
    for name in TOKEN_NAMES:
        if tok := vault.secret(name):
            return tok
    return None


def _get(client, url: str, token: str | None = None, **params):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "homeshed-repo-stats"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.get(url, headers=headers, params=params or None, timeout=10)


def _pypi(client, repo: str) -> dict | None:
    """Downloads from pypistats.org for the package named like the repo, only if its PyPI page links the repo."""
    name = repo.split("/")[1].lower()
    try:
        meta = client.get(f"https://pypi.org/pypi/{name}/json", timeout=8)
        if meta.status_code != 200:
            return None
        info = meta.json().get("info") or {}
        links = " ".join([str(info.get("home_page") or ""), *map(str, (info.get("project_urls") or {}).values())])
        if f"github.com/{repo}".lower() not in links.lower():
            return None
        out = {"package": name, "version": info.get("version"), "url": f"https://pypi.org/project/{name}/"}
        recent = client.get(f"https://pypistats.org/api/packages/{name}/recent", timeout=8)
        if recent.status_code == 200:
            d = recent.json().get("data") or {}
            out.update(day=d.get("last_day"), week=d.get("last_week"), month=d.get("last_month"))
        else:
            out["note"] = "pypistats has no figures yet (it counts a day behind)"
        return out
    except Exception:  # noqa: BLE001 - PyPI or pypistats down: no PyPI line
        return None


def _load() -> dict:
    try:
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(repo: str, today: str, counts: dict, traffic: dict | None) -> dict:
    """Merge today's counts and GitHub's 14 days of per-day traffic into the kept history; returns this repo's record."""
    with _lock:
        data = _load()
        rec = data.setdefault(repo, {"days": {}, "traffic": {}})
        rec["days"][today] = counts
        for kind in ("views", "clones"):
            for row in (traffic or {}).get(kind) or []:
                day = str(row.get("timestamp") or "")[:10]
                if day:
                    rec["traffic"].setdefault(day, {})[kind] = [int(row.get("count") or 0), int(row.get("uniques") or 0)]
        for key in ("days", "traffic"):
            for day in sorted(rec[key])[:-KEEP_DAYS]:
                del rec[key][day]
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(HISTORY)
        return rec


def _since(rec: dict, today: str, days: int, key: str) -> int | None:
    """How much a count grew over the last `days` days, from the kept daily notes (None until there's an older one)."""
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.mktime(time.strptime(today, "%Y-%m-%d")) - days * 86400))
    older = [d for d in sorted(rec["days"]) if d <= cutoff] or [d for d in sorted(rec["days"]) if d < today]
    if not older:
        return None
    return (rec["days"][today].get(key) or 0) - (rec["days"][older[-1]].get(key) or 0)


def collect(repo: str) -> dict:
    import httpx
    token = _token()
    with httpx.Client() as client:
        r = _get(client, f"{API}/repos/{repo}", token)
        if r.status_code != 200:
            raise StatsError(f"GitHub answered {r.status_code} for {repo}: is the name right and the repo public?")
        info = r.json()
        rel = _get(client, f"{API}/repos/{repo}/releases", token, per_page=30)
        releases = [{"tag": x.get("tag_name"), "published": x.get("published_at"),
                     "downloads": sum(int(a.get("download_count") or 0) for a in x.get("assets") or [])}
                    for x in (rel.json() if rel.status_code == 200 else []) if isinstance(x, dict)]
        traffic, why = None, ""
        if token:
            views = _get(client, f"{API}/repos/{repo}/traffic/views", token)
            if views.status_code == 200:
                clones = _get(client, f"{API}/repos/{repo}/traffic/clones", token)
                refs = _get(client, f"{API}/repos/{repo}/traffic/popular/referrers", token)
                traffic = {"views": views.json().get("views") or [],
                           "clones": (clones.json().get("clones") or []) if clones.status_code == 200 else [],
                           "referrers": [{"from": x.get("referrer"), "views": x.get("count"), "people": x.get("uniques")}
                                         for x in (refs.json() if refs.status_code == 200 else [])[:5]]}
            else:
                why = "the GitHub token can't read this repo's traffic (it needs push access to the repo)"
        else:
            why = "add a GitHub token in Settings > Credentials (GITHUB_STATS_TOKEN) to see views and clones"
        pypi = _pypi(client, repo)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    counts = {"stars": info.get("stargazers_count") or 0, "forks": info.get("forks_count") or 0,
              "release_downloads": sum(x["downloads"] for x in releases)}
    rec = _remember(repo, today, counts, traffic)
    out = {"repo": repo, "url": info.get("html_url"), "private": bool(info.get("private")), **counts,
           "watchers": info.get("subscribers_count") or 0, "open_issues": info.get("open_issues_count") or 0,
           "stars_week": _since(rec, today, 7, "stars"), "releases": releases[:5], "pypi": pypi, "checked": time.time()}
    if traffic is not None:
        t = rec["traffic"]
        recent = sorted(t)[-14:]
        out["traffic"] = {
            "views_14d": sum(t[d].get("views", [0, 0])[0] for d in recent),
            "clones_14d": sum(t[d].get("clones", [0, 0])[0] for d in recent),
            "views_total": sum(v.get("views", [0, 0])[0] for v in t.values()),
            "clones_total": sum(v.get("clones", [0, 0])[0] for v in t.values()),
            "since": min(t) if t else today, "referrers": traffic["referrers"],
            "daily": [{"day": d, "views": t[d].get("views", [0, 0])[0], "clones": t[d].get("clones", [0, 0])[0]}
                      for d in recent]}
    else:
        out["traffic_note"] = why
    return out


@tool(name="stats", category="repo", doc="repo/stats.md")
def stats(repo: str) -> dict:
    """How a GitHub repository is doing: stars, forks, downloads, and views and clones when a token allows.

    Args:
        repo: owner/name, as on GitHub (e.g. "octocat/hello-world").

    Returns:
        {repo, url, private, stars, forks, watchers, open_issues, stars_week, release_downloads,
        releases: [{tag, published, downloads}], pypi: {package, version, url, day, week, month} or null,
        traffic: {views_14d, clones_14d, views_total, clones_total, since, referrers, daily} or traffic_note, checked}.

    Raises:
        StatsError: a malformed name, or GitHub didn't answer for it.
    """
    repo = str(repo or "").strip()
    if not REPO.match(repo):
        raise StatsError("repo must be owner/name, as on GitHub")
    hit = _cache.get(repo.lower())
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    out = collect(repo)
    _cache[repo.lower()] = (time.time(), out)
    return out
