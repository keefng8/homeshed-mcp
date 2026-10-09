"""repo.leak_scan. See ../../capabilities/repo/leak-scan.md.

Private details and secrets in a folder that's about to go public, reported as file, line and pattern name, never the
matched text. From the Github prepper's release tools (sync_public.py's scan, 2026-09-29/30), which found 432 private
details in 82 files of a "clean" project. Private-detail patterns come from the call or the folder's own
.repo-release.json (the prepper's keys: leak_patterns, leak_exempt, allowed_examples, public_identity); nothing private
ships with the tool. Secrets use transcript_scan's kinds, and show at most a 4-character prefix, only for kinds whose
start is public anyway. Caller patterns are capped and checked for the usual runaway shape, since Python's re has no
timeout. Read-only, inside READ_ALLOWED_ROOTS.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import findings
from registry import tool

CONFIG = ".repo-release.json"
MAX_PATTERNS, MAX_PATTERN_LEN = 50, 300
MAX_FILES, MAX_FILE_BYTES, MAX_LINE = 5000, 1_000_000, 2000
# a quantified group with an unescaped quantifier inside, e.g. (a+)+ or (\w*)*: can backtrack for ever on a long line
NESTED = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)(?:[+*]|\{\d)")
ALLOW_MARK = re.compile(r"(?:secret|leak)-scan:\s*allow")
SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache"}


class LeakScanError(RuntimeError):
    """A bad folder, too many or unsafe patterns, an unreadable config, or a bad detail value."""


def _patterns(raw: dict) -> dict[str, re.Pattern]:
    if not isinstance(raw, dict):
        raise LeakScanError("patterns must be {name: regex}")
    if len(raw) > MAX_PATTERNS:
        raise LeakScanError(f"at most {MAX_PATTERNS} patterns")
    out = {}
    for name, rx in raw.items():
        if not isinstance(rx, str) or not rx or len(rx) > MAX_PATTERN_LEN:
            raise LeakScanError(f"pattern {name!r}: 1 to {MAX_PATTERN_LEN} characters")
        if NESTED.search(rx):
            raise LeakScanError(f"pattern {name!r} nests quantifiers, e.g. (a+)+, which can run for ever: simplify it")
        try:
            out[str(name)[:60]] = re.compile(rx)
        except re.error as exc:
            raise LeakScanError(f"pattern {name!r} isn't a valid regex: {exc}") from None
    return out


def _config(root: Path) -> dict:
    path = root / CONFIG
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LeakScanError(f"{CONFIG} isn't readable JSON: {exc}") from None
    return data if isinstance(data, dict) else {}


def _files(root: Path):
    seen = 0
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(base) / d / "pyvenv.cfg").is_file())
        for name in sorted(files):
            path = Path(base) / name
            if name == CONFIG:
                continue  # it holds the private patterns themselves
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:1024]:
                continue  # binary
            try:
                yield path.relative_to(root).as_posix(), data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            seen += 1
            if seen >= MAX_FILES:
                return


@tool(name="leak_scan", category="repo", doc="repo/leak-scan.md")
def leak_scan(folder: str = ".", patterns: dict | None = None, secrets: bool = True, detail: str = "compact") -> dict:
    """Private details and secrets in a folder about to go public: file, line and pattern, never the matched text.

    Args:
        folder: the folder to scan (must be inside READ_ALLOWED_ROOTS; see readroots.py).
        patterns: {name: regex} for your private details (names, domains, private addresses, folder paths). Default:
            leak_patterns from the folder's .repo-release.json, which may also give leak_exempt ({name: [path
            prefixes]}), allowed_examples and public_identity (literal text that's fine to publish). At most 50
            patterns of 300 characters each.
        secrets: also look for secrets (API keys, tokens, private keys, passwords in settings). Default true.
        detail: "compact" (the default: totals and at most 20 findings) or "full" (every finding).

    Returns:
        {folder, source: "call" | ".repo-release.json" | "none", files: int, checks: [{id (the pattern or secret
        kind), kind: "leak" | "secret", ok: false, file, line, prefix?}], failed, counts, passed, more?}. prefix is
        the first 4 characters, only for secret kinds whose start is public anyway. A line marked
        "secret-scan: allow" or "leak-scan: allow" is skipped.

    Raises:
        LeakScanError: the folder is missing, not a folder, or outside the allowed folders; a pattern is invalid, too
            long or nests quantifiers; more than 50 patterns; an unreadable config; or a bad detail value.
    """
    findings.check_detail(detail, LeakScanError)
    import readroots
    try:
        root = Path(readroots.resolve_folder(folder))
    except readroots.OutsideRoots as exc:
        raise LeakScanError(str(exc)) from None
    config = _config(root)
    source = "call" if patterns else (CONFIG if config.get("leak_patterns") else "none")
    leaks = _patterns(patterns if patterns else (config.get("leak_patterns") or {}))
    exempt = {k: [str(p) for p in v] for k, v in (config.get("leak_exempt") or {}).items() if isinstance(v, list)}
    literals = [str(x) for x in (config.get("allowed_examples") or []) + (config.get("public_identity") or []) if x]
    kinds = []
    if secrets:
        from tools.secrets.transcript_scan import KINDS
        kinds = [(kind, rx, public) for kind, rx, _action, public in KINDS]

    rows, files = [], 0
    for rel, text in _files(root):
        files += 1
        skip = {name for name, prefixes in exempt.items() if any(rel.startswith(p) for p in prefixes)}
        for no, line in enumerate(text.splitlines(), 1):
            if len(line) > MAX_LINE or ALLOW_MARK.search(line):
                continue
            for literal in literals:
                line = line.replace(literal, "")
            for name, rx in leaks.items():
                if name not in skip and rx.search(line):
                    rows.append({"id": name, "kind": "leak", "ok": False, "file": rel, "line": no})
            for kind, rx, public in kinds:
                m = rx.search(line)
                if m:
                    value = m.group(m.lastindex or 0)
                    rows.append({"id": kind, "kind": "secret", "ok": False, "file": rel, "line": no,
                                 **({"prefix": value[:4]} if public else {})})
                    break  # the most specific kind wins, as in transcript_scan
    result = {"folder": str(root), "source": source, "files": files, "checks": rows, "failed": len(rows)}
    return findings.compact(result, detail)
