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
from collections import Counter
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
# this scan reads one line at a time, so the header alone must count: transcript_scan's pattern wants the key's body too
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")


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


def _files(root: Path, not_checked: Counter):
    """Each text file as (relative path, text). Whatever it can't read is counted in not_checked, never just dropped."""
    seen = 0
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(base) / d / "pyvenv.cfg").is_file())
        for name in sorted(files):
            path = Path(base) / name
            if name == CONFIG:
                continue  # it holds the private patterns themselves
            if seen >= MAX_FILES:
                not_checked["over_file_limit"] += 1
                continue
            try:
                with open(path, "rb") as handle:
                    data = handle.read(MAX_FILE_BYTES + 1)
            except OSError:
                not_checked["unreadable"] += 1
                continue
            wide = data.startswith((b"\xff\xfe", b"\xfe\xff"))  # UTF-16, what Windows PowerShell's > writes
            if not wide and b"\0" in data[:1024]:
                not_checked["binary"] += 1
                continue
            if len(data) > MAX_FILE_BYTES:
                not_checked["too_big"] += 1
                continue
            # secrets are plain ASCII, so a stray byte in another encoding must not hide the whole file
            yield path.relative_to(root).as_posix(), data.decode("utf-16" if wide else "utf-8", "replace")
            seen += 1


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
        {folder, source: "call" | ".repo-release.json" | "none", files: int, clean: bool, not_checked: {reason: count},
        checks: [{id (the pattern or secret kind), kind: "leak" | "secret", ok: false, file, line, prefix?}], failed,
        counts, passed, note?, more?}. clean is true only when nothing was found, at least one file was read, no text
        went unread, and the folder isn't the server's own by default: failed 0 alone doesn't mean clean. not_checked
        reasons: binary (doesn't affect clean), too_big,
        long_lines, unreadable, over_file_limit. prefix is the first 4 characters, only for secret kinds whose start
        is public anyway. A line marked "secret-scan: allow" or "leak-scan: allow" is skipped.

    Raises:
        LeakScanError: the folder is missing, not a folder, or outside the allowed folders; a pattern is invalid, too
            long or nests quantifiers; more than 50 patterns; an unreadable config; a bad detail value; or nothing to
            check (secrets off and no patterns).
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
        kinds = [(kind, PRIVATE_KEY if kind == "private key" else rx, public) for kind, rx, _action, public in KINDS]
    if not leaks and not kinds:
        raise LeakScanError("nothing to check: secrets is off and there are no patterns, so no result could mean clean")

    rows, files, not_checked = [], 0, Counter()
    for rel, text in _files(root, not_checked):
        files += 1
        skip = {name for name, prefixes in exempt.items() if any(rel.startswith(p) for p in prefixes)}
        for no, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARK.search(line):
                continue
            if len(line) > MAX_LINE:
                not_checked["long_lines"] += 1
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
    own_folder = folder == "." and Path(__file__).resolve().is_relative_to(root)
    clean = not rows and files > 0 and not set(not_checked) - {"binary"} and not own_folder
    result = {"folder": str(root), "source": source, "files": files, "clean": clean, "not_checked": dict(not_checked),
              "checks": rows, "failed": len(rows)}
    if own_folder:
        result["note"] = "no folder given, so this scanned the server's own folder, not your project: pass its full path"
    return findings.compact(result, detail)
