"""secrets.transcript_scan. See ../../capabilities/secrets/transcript-scan.md.

Claude Code keeps every tool call and its output, in plain text, for good (<config>/projects/**/*.jsonl). Keys pasted
into a prompt or printed by a command stay there. On 2026-09-29 a careful owner's PC held 11 live ones (KB-0015).
This scans only those transcripts and returns what was found and where to rotate it, never the secret: kind, the
first 4 characters (only for kinds whose prefix is public anyway), length, a SHA-256 fingerprint prefix, the
projects, the file and occurrence counts, and first and last seen. Ported from scripts/transcript_secrets.py (R&D,
2026-09-30); patterns from scripts/secret_scan.py, extended with provider-specific kinds.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import findings
import readroots
from registry import tool

MAX_BYTES = 200_000_000   # a single transcript larger than this is skipped (and reported)
MAX_FINDINGS = 50
FP_CHARS = 12
MIN_LEN = 12

# (kind, pattern, what to do, public prefix?) Most specific first: a value takes the first kind that matches it.
KINDS = [
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[A-Za-z0-9+/=\s]{40,}"),
     "make a new key pair, and remove the old public key wherever it's authorised (servers, GitHub)", True),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "rotate it at console.anthropic.com > API keys", True),
    ("OpenRouter key", re.compile(r"\bsk-or-[A-Za-z0-9_-]{20,}"), "rotate it at openrouter.ai/keys", True),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"), "rotate it at platform.openai.com/api-keys "
     "(or at whichever provider issued this sk- key)", True),
    ("Groq key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}"), "rotate it at console.groq.com/keys", True),
    ("NVIDIA key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{20,}"), "rotate it at build.nvidia.com > API keys", True),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "rotate it at aistudio.google.com/apikey (Gemini) or "
     "console.cloud.google.com > Credentials", True),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
     "revoke it at github.com/settings/tokens and make a new one", True),
    ("Stripe key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{20,}"), "roll it at dashboard.stripe.com/apikeys", True),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "regenerate it at api.slack.com/apps", True),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "deactivate it in AWS IAM > Security credentials, "
     "then make a new one", True),
    ("HomeShed client token", re.compile(r"\bhlc_[A-Za-z0-9_-]{20,}"), "issue that client a new token on your HomeShed "
     "server (POST /clients/<name>/rotate with the owner token)", True),
    ("Bearer token", re.compile(r"Bearer\s+([A-Za-z0-9._~+/=-]{20,})"), "rotate it at the service it signs in to", False),
    ("password or secret", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|auth[_-]?token)\b[\"']?\s*[=:]\s*"
                                      r"[\"']([^\"'\s]{8,})[\"']"), "change it where it's set, and anywhere it's reused", False),
    ("token in a setting", re.compile(r"(?i)\b\w*(?:bearer|token|_key|secret)\b[\"']?\s*[=:]\s*[\"']([^\"'\s$]{16,})[\"']"),
     "change it where it's set, and anywhere it's reused", False),
]
try:  # an install's own key kinds (private_secret_kinds.py; the public copy has none), before the three generic ones
    from private_secret_kinds import KINDS as _OWN
    KINDS[-3:-3] = _OWN
except ImportError:
    pass
PLACEHOLDER = re.compile(r"(?i)hidden|redacted|example|placeholder|your[-_ ]?|xxxx|\*\*\*|<[^>]*>|\$\{|changeme|dummy|test")


class TranscriptScanError(ValueError):
    """Bad input: days out of range, or detail not compact/full."""


def projects_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:FP_CHARS]


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def _is_placeholder(value: str) -> bool:
    return len(value) < MIN_LEN or len(set(value)) < 6 or bool(PLACEHOLDER.search(value))


def _values(text: str):
    """(kind index, value) for each secret-looking value; a value counts once, as its most specific kind."""
    seen = set()
    for i, (_kind, rx, _do, _public) in enumerate(KINDS):
        for m in rx.finditer(text):
            value = (m.group(m.lastindex) if m.lastindex else m.group(0)).strip("\\\"'`,;)")
            if value in seen or _is_placeholder(value):
                continue
            seen.add(value)
            yield i, value


def _day(ts, fallback: float) -> str:
    if isinstance(ts, str) and len(ts) >= 10:
        return ts[:10]
    return datetime.fromtimestamp(fallback, tz=timezone.utc).strftime("%Y-%m-%d")


@tool(name="transcript_scan", category="secrets", doc="secrets/transcript-scan.md")
def transcript_scan(days: int = 0, detail: str = "compact") -> dict:
    """Find secrets saved in Claude Code's transcripts, and say where to rotate each one. Never returns a secret.

    Args:
        days: only transcripts changed in the last N days; 0 (the default) scans them all, since a saved secret
            stays until the file is deleted.
        detail: "compact" (the default: totals and at most 20 findings) or "full" (every finding).

    Returns:
        {folder, transcripts, skipped, summary: {kind: count}, actions: {kind: what to do}, checks: [{id, kind,
        ok: false, prefix, length, fingerprint, projects, files, occurrences, first_seen, last_seen}], failed, counts,
        more?}. Each kind's action is given once in `actions`, not per row (compact output, reflection #7).
        "prefix" is empty for kinds whose start isn't public (passwords, bearer tokens).

    Raises:
        TranscriptScanError: days below 0, or detail not compact/full.
    """
    findings.check_detail(detail, TranscriptScanError)
    if days < 0:
        raise TranscriptScanError("days must be 0 (all) or more")
    root = projects_dir()
    out = {"folder": str(root), "transcripts": 0, "skipped": 0, "summary": {}, "actions": {}, "checks": [], "failed": 0}
    if not root.is_dir():
        out["note"] = "no Claude Code transcripts on this machine"
        return out
    real = root.resolve()
    since = time.time() - days * 86400 if days else 0
    found: dict[str, dict] = {}
    for path in real.rglob("*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime < since or not readroots.inside(real, path):
            continue
        if st.st_size > MAX_BYTES:
            out["skipped"] += 1
            continue
        out["transcripts"] += 1
        project = path.relative_to(real).parts[0]
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            out["skipped"] += 1
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                record, texts = {}, [line]
            else:
                texts = list(_strings(record))
            day = _day(record.get("timestamp") if isinstance(record, dict) else None, st.st_mtime)
            for text in texts:
                for i, value in _values(text):
                    fp = _fingerprint(value)
                    f = found.get(fp)
                    if f is None:
                        kind, _rx, action, public = KINDS[i]
                        f = found[fp] = {"id": "secret", "kind": kind, "ok": False,
                                         "prefix": value[:4] if public else "", "length": len(value), "fingerprint": fp,
                                         "projects": set(), "files": set(), "occurrences": 0,
                                         "first_seen": day, "last_seen": day}
                        out["actions"][kind] = action
                    f["projects"].add(project)
                    f["files"].add(str(path))
                    f["occurrences"] += 1
                    f["first_seen"], f["last_seen"] = min(f["first_seen"], day), max(f["last_seen"], day)
    rows = []
    for f in sorted(found.values(), key=lambda f: (f["last_seen"], f["occurrences"]), reverse=True):
        rows.append({**f, "projects": sorted(f["projects"])[:5], "files": len(f["files"])})
        out["summary"][f["kind"]] = out["summary"].get(f["kind"], 0) + 1
    out["checks"], out["failed"] = rows, len(rows)
    return findings.compact(out, detail)
