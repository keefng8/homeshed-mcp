"""strapi.check. See ../../capabilities/strapi/check.md.

Each check guards a real, repeated Strapi bug:
  KB-0019  missing pagination (Strapi's default page size is 25, silently)
  KB-0020  Strapi v4 `.attributes` shapes read in v5 code (v5 records are flat)
  KB-0021  a relation filter path that isn't in any schema, so the query silently returns no rows
  KB-0027  NODE_ENV=development left in the Dockerfile's runtime stage
  KB-0042  (source-side cousin) a custom route that declares /api itself, giving /api/api/*
  (no KB)  a public /admin panel answering with no gate in front of it (only when a url is given)
Regex scans of the source, not a JavaScript parser: see the doc's "Known limitations".
"""
from __future__ import annotations

import http.client
import json
import os
import re
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import findings
import netguard
import readroots
from registry import tool

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".cache", ".strapi", ".tmp", "coverage"}
MAX_FILES = 5000
MAX_BYTES = 1_000_000
SYSTEM_FIELDS = {"id", "documentId", "$and", "$or", "$not", "createdAt", "updatedAt", "publishedAt", "locale"}

_API_CALL = re.compile(r"(?:fetch|axios(?:\.(?:get|post))?|got)\s*\(\s*[`'\"][^`'\"]*?/api/[^`'\"]*?[`'\"][^)]*\)", re.DOTALL)
_V4_ATTRIBUTES = re.compile(r"\b(?:record|entry|item|data|result|row|feature)s?\.attributes\.")
_BRACKET_FILTER = re.compile(r"filters\[([A-Za-z0-9_$]+)\]")
_OBJECT_FILTER = re.compile(r"filters\s*:\s*\{\s*([A-Za-z0-9_$]+)\s*:")
_NODE_ENV_DEV = re.compile(r"ENV\s+NODE_ENV[=\s]+development\b")
_ROUTE_PATH = re.compile(r"path\s*:\s*['\"]([^'\"]+)['\"]")


class StrapiCheckError(ValueError):
    """Bad input: a folder outside the allowed roots, or a url that isn't http(s)."""


def _files(root: Path, sub: str, suffix: str):
    """Files under root/sub with this suffix, skipping build folders and anything a symlink points outside root."""
    base = root / sub
    count = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if not name.endswith(suffix) or not readroots.inside(root, path):
                continue
            count += 1
            if count > MAX_FILES:
                return
            yield path


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


class _Report:
    def __init__(self, root: Path):
        self.root, self.checks = root, []

    def fail(self, cid: str, kb: str | None, detail: str, path: Path | None = None, line: int | None = None):
        self.checks.append({"id": cid, "kb": kb, "ok": False, "detail": detail,
                            "file": str(path.relative_to(self.root)) if path else None, "line": line})

    def passed(self, cid: str, kb: str | None, detail: str = "", skipped: bool = False):
        row = {"id": cid, "kb": kb, "ok": True, "detail": detail, "file": None, "line": None}
        if skipped:
            row["skipped"] = True
        self.checks.append(row)


def _pagination(r: _Report):
    found = False
    for path in _files(r.root, "src", ".js"):
        text = _read(path)
        for m in _API_CALL.finditer(text):
            if "pagination" in m.group(0) or "$limit" in m.group(0):
                continue
            found = True
            r.fail("missing-pagination", "KB-0019", "an /api/ call with no pagination: Strapi returns 25 rows by default",
                   path, _line(text, m.start()))
    if not found:
        r.passed("missing-pagination", "KB-0019")


def _v4_attributes(r: _Report):
    found = False
    for path in _files(r.root, "src", ".js"):
        text = _read(path)
        for m in _V4_ATTRIBUTES.finditer(text):
            found = True
            r.fail("v4-attributes", "KB-0020", "a v4 `.attributes.` read; Strapi v5 records are flat", path, _line(text, m.start()))
    if not found:
        r.passed("v4-attributes", "KB-0020")


def _schema_fields(root: Path) -> set[str]:
    fields: set[str] = set()
    for path in _files(root, os.path.join("src", "api"), "schema.json"):
        try:
            attrs = json.loads(_read(path) or "{}").get("attributes", {})
        except (ValueError, AttributeError):
            continue
        if isinstance(attrs, dict):
            fields.update(attrs)
    return fields


def _filter_paths(r: _Report):
    known = _schema_fields(r.root)
    if not known:
        r.passed("filter-path", "KB-0021", "no content-type schemas under src/api", skipped=True)
        return
    found = False
    for path in _files(r.root, "src", ".js"):
        text = _read(path)
        for pattern in (_BRACKET_FILTER, _OBJECT_FILTER):
            for m in pattern.finditer(text):
                segment = m.group(1)
                if segment in SYSTEM_FIELDS or segment in known:
                    continue
                found = True
                r.fail("filter-path", "KB-0021", f"filter on {segment!r}, which no schema declares: the query returns no rows",
                       path, _line(text, m.start()))
    if not found:
        r.passed("filter-path", "KB-0021", "first path segment only")


def _node_env(r: _Report):
    dockerfile = r.root / "Dockerfile"
    if not dockerfile.is_file() or not readroots.inside(r.root, dockerfile):
        r.passed("node-env", "KB-0027", "no Dockerfile", skipped=True)
        return
    lines = _read(dockerfile).splitlines()
    froms = [i for i, line in enumerate(lines) if line.strip().upper().startswith("FROM")]
    if not froms:
        r.passed("node-env", "KB-0027", "no FROM instruction", skipped=True)
        return
    for offset, line in enumerate(lines[froms[-1]:]):
        if _NODE_ENV_DEV.search(line):
            r.fail("node-env", "KB-0027", "NODE_ENV=development in the runtime stage (after the last FROM)",
                   dockerfile, froms[-1] + offset + 1)
            return
    r.passed("node-env", "KB-0027")


def _route_prefixes(r: _Report):
    found = False
    for path in _files(r.root, os.path.join("src", "api"), ".js"):
        if path.parent.name != "routes":
            continue
        text = _read(path)
        for m in _ROUTE_PATH.finditer(text):
            route = m.group(1)
            if route == "/api" or route.startswith("/api/"):
                found = True
                r.fail("route-api-prefix", "KB-0042", f"route path {route!r}: Strapi already serves custom routes under /api",
                       path, _line(text, m.start()))
    if not found:
        r.passed("route-api-prefix", "KB-0042")


def _probe(url: str, timeout: float = 8.0) -> int:
    """GET <url>/admin once, to the netguard-vetted address (no second DNS lookup), no redirects followed."""
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
    address = netguard.resolve_permitted(host)[0]
    path = (parts.path.rstrip("/") or "") + "/admin"
    if parts.scheme == "https":
        ctx = ssl.create_default_context()

        class _Conn(http.client.HTTPSConnection):
            def connect(self):
                import socket
                sock = socket.create_connection((address, port), timeout=timeout)
                self.sock = ctx.wrap_socket(sock, server_hostname=host)
        conn = _Conn(host, port, timeout=timeout, context=ctx)
    else:
        conn = http.client.HTTPConnection(address, port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Host": host, "User-Agent": "strapi-check/1.0"})
        return conn.getresponse().status
    finally:
        conn.close()


def _admin(r: _Report, url: str):
    if not url:
        r.passed("admin-exposed", None, "no url given", skipped=True)
        return
    try:
        code = _probe(url)
    except netguard.BlockedHost as e:
        if str(e).startswith("could not resolve"):
            r.fail("admin-exposed", None, f"couldn't reach {url}: the name doesn't resolve")
        else:
            r.passed("admin-exposed", None, f"not checked: {e} (NETWORK_ALLOWED_CIDRS allows private ranges)", skipped=True)
        return
    except (OSError, http.client.HTTPException, ssl.SSLError) as e:
        r.fail("admin-exposed", None, f"couldn't reach {url.rstrip('/')}/admin: {e}")
        return
    if 200 <= code < 300:
        r.fail("admin-exposed", None, f"{url.rstrip('/')}/admin answered {code}: reachable with no gate in front")
    else:
        r.passed("admin-exposed", None, f"/admin answered {code}")


@tool(name="check", category="strapi", doc="strapi/check.md")
def check(repo: str, url: str = "", detail: str = "compact") -> dict:
    """Lint a Strapi v5 project for six known, silent bugs, and optionally probe its live /admin.

    Args:
        repo: the project folder (must be inside READ_ALLOWED_ROOTS; see readroots.py).
        url: the live site's base address, for the public /admin check. Optional; public hosts only unless
            NETWORK_ALLOWED_CIDRS allows a private range.
        detail: "compact" (the default: totals, at most 20 failing rows) or "full" (every row).

    Returns:
        {repo, checks: [{id, kb, ok, detail, file, line, skipped?}], failed, counts, passed, more?}. file is
        relative to repo.

    Raises:
        StrapiCheckError: repo outside the allowed roots or not a folder, or a url that isn't http(s).
    """
    findings.check_detail(detail, StrapiCheckError)
    try:
        root = readroots.resolve_folder(repo)
    except readroots.OutsideRoots as e:
        raise StrapiCheckError(str(e)) from None
    url = (url or "").strip()
    if url:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise StrapiCheckError("url must be http(s)://host with no credentials in it")
    r = _Report(root)
    for step in (_pagination, _v4_attributes, _filter_paths, _node_env, _route_prefixes):
        step(r)
    _admin(r, url)
    return findings.compact({"repo": str(root), "checks": r.checks, "failed": sum(1 for c in r.checks if not c["ok"])},
                            detail)
