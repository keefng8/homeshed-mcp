"""code.webhook_retry_check. See ../../capabilities/code/webhook-retry-check.md.

The bug (seen in a real PayPal webhook): a `catch` that logs and falls through to an implicit 200. The provider only retries a delivery that got a
non-2xx, so a swallowed exception silently loses the retry; there, a paid membership grant.
In a catch block, these count as signalling failure: ctx.status = 4xx/5xx, ctx.throw(...), a bare throw, and
res.status(4xx/5xx). A handler with no try/catch isn't a violation (an uncaught error becomes a 500) and is listed as
information. Regex and brace matching over JavaScript text, not a parser: see the doc's "Known limitations".
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import findings
import readroots
from registry import tool

SKIP_DIRS = {"node_modules", "dist", "build", ".git", ".cache", ".strapi", "coverage"}
MAX_FILES = 5000
MAX_BYTES = 1_000_000

_FUNCS = [
    re.compile(r"async\s+function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"),         # async function name(...) {
    re.compile(r"([A-Za-z_$][\w$]*)\s*:\s*async\s*\([^)]*\)\s*=>\s*\{"),           # name: async (...) => {
    re.compile(r"([A-Za-z_$][\w$]*)\s*=\s*async\s*\([^)]*\)\s*=>\s*\{"),           # name = async (...) => {
    re.compile(r"\basync\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"),                  # async name(...) {  (Strapi style)
]
_TRY = re.compile(r"\btry\s*\{")
_CATCH = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{")
_STATUS_ASSIGN = re.compile(r"ctx\.status\s*=\s*([1-5]\d{2})\b")
_RES_STATUS = re.compile(r"res\.status\s*\(\s*([1-5]\d{2})\s*\)")
_CTX_THROW = re.compile(r"ctx\.throw\s*\(")
_BARE_THROW = re.compile(r"\bthrow\b")


class WebhookCheckError(ValueError):
    """Bad input: a folder outside the allowed roots, or a handler pattern that isn't a valid regex."""


def _block(text: str, open_brace: int) -> tuple[str, int]:
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace:i + 1], i + 1
    return text[open_brace:], len(text)


def _functions(text: str, name_re: re.Pattern) -> list[tuple[int, str]]:
    found: dict[int, str] = {}
    for pattern in _FUNCS:
        for m in pattern.finditer(text):
            if name_re.search(m.group(1)):
                found.setdefault(m.end() - 1, m.group(1))
    return sorted(found.items())


def _try_catches(body: str) -> list[tuple[int, str]]:
    pairs = []
    for m in _TRY.finditer(body):
        _t, end = _block(body, m.end() - 1)
        j = end
        while j < len(body) and body[j] in " \t\r\n":
            j += 1
        cm = _CATCH.match(body, j)
        if cm:
            pairs.append((m.start(), _block(body, cm.end() - 1)[0]))
    return pairs


def _signals_failure(catch: str) -> bool:
    for pattern in (_STATUS_ASSIGN, _RES_STATUS):
        m = pattern.search(catch)
        if m and int(m.group(1)) >= 400:
            return True
    return bool(_CTX_THROW.search(catch) or _BARE_THROW.search(catch))


@tool(name="webhook_retry_check", category="code", doc="code/webhook-retry-check.md")
def webhook_retry_check(folder: str, handler_pattern: str = "webhook", exclude: list[str] | None = None,
                        detail: str = "compact") -> dict:
    """Find webhook handlers whose catch block swallows the error (an implicit 200), so the sender never retries.

    Args:
        folder: the project folder to scan for .js files (must be inside READ_ALLOWED_ROOTS; see readroots.py).
        handler_pattern: a case-insensitive regex a function's name must match to be checked (default "webhook").
        exclude: extra folder names to skip, on top of node_modules, dist, build and .git.
        detail: "compact" (the default: totals, at most 20 failing rows) or "full" (every row).

    Returns:
        {folder, checks: [{id, ok, detail, file, line, info?}], failed: int, handlers: int}. ids: "swallowed-error"
        (fails), "signals-failure" (passes), "no-try-catch" (info). file is relative to folder.

    Raises:
        WebhookCheckError: folder outside the allowed roots or not a folder, or a bad handler_pattern.
    """
    findings.check_detail(detail, WebhookCheckError)
    try:
        root = readroots.resolve_folder(folder)
    except readroots.OutsideRoots as e:
        raise WebhookCheckError(str(e)) from None
    try:
        name_re = re.compile(handler_pattern or "webhook", re.IGNORECASE)
    except re.error as e:
        raise WebhookCheckError(f"handler_pattern isn't a valid regex: {e}") from None
    skip = SKIP_DIRS | set(exclude or [])
    checks, count = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            path = Path(dirpath) / name
            if not name.endswith(".js") or not readroots.inside(root, path):
                continue
            count += 1
            if count > MAX_FILES:
                break
            try:
                if path.stat().st_size > MAX_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = str(path.relative_to(root))
            for brace, fname in _functions(text, name_re):
                body, _ = _block(text, brace)
                line = text.count("\n", 0, brace) + 1
                pairs = _try_catches(body)
                if not pairs:
                    checks.append({"id": "no-try-catch", "ok": True, "info": True, "file": rel, "line": line,
                                   "detail": f"{fname}: no try/catch"})
                elif any(_signals_failure(c) for _p, c in pairs):
                    checks.append({"id": "signals-failure", "ok": True, "file": rel, "line": line,
                                   "detail": f"{fname}: catch returns a failure"})
                else:
                    try_pos, catch = pairs[0]
                    checks.append({"id": "swallowed-error", "ok": False, "file": rel,
                                   "line": text.count("\n", 0, brace + try_pos) + 1,
                                   "detail": f"{fname}: catch swallows the error; starts {' '.join(catch[:48].split())}"})
    # What each id means is said once here, not in every row (compact output, reflection #7)
    return findings.compact({"folder": str(root), "checks": checks, "failed": sum(1 for c in checks if not c["ok"]),
                             "handlers": len(checks), "meaning": {
                                 "swallowed-error": "the catch never signals failure (no ctx.status >= 400, ctx.throw, "
                                                    "throw or res.status >= 400), so the sender sees 'delivered' and "
                                                    "never retries",
                                 "signals-failure": "the catch returns a failure, so the sender retries",
                                 "no-try-catch": "no try/catch: an error becomes a 500 by default"}}, detail)
