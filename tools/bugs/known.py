"""bugs.report / bugs.find / bugs.list. See ../../capabilities/bugs/known.md.

Known bugs. Every mistake found once is recorded with its cause and its fix, so no AI session, and no end user,
has to rediscover it. Schema after ITIL's known-error database: "workaround exists, no fix" is a real state of its own, and a normalised error
fingerprint finds the same bug again when its surface text differs (numbers, paths, hex, quotes).

Every text field is redacted before it's stored: a bug report must never become a way to leak a secret.
First draft by the local model (qwen3-coder-30b); fixed on review: empty fingerprints matched each other, a corrupt
file was silently emptied, a new occurrence reset a fixed bug to "open", and the redaction took "mask-rcnn" for a key.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from registry import tool
from paths import data_path

BUGS_FILE = Path(os.environ.get("KNOWN_BUGS_FILE") or data_path("observations/known_bugs.json"))
STATUSES = ("open", "workaround-only", "fixed")
BUG_ID = re.compile(r"(?:PRO-)?KB-\d{4}")
TEXT_LIMITS = {"title": 120, "symptom": 600, "error_text": 1500, "root_cause": 800, "workaround": 600,
               "fix": 800, "component": 60, "guard": 80}
_lock = threading.Lock()
_SECRETS = [
    re.compile(r"\b(?:hlc_|nvapi-|gh[pousr]_|github_pat_|sk-|xox[abprs]-)[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}(?=\s|$|[\"',;)])"),
]
_ASSIGNED = re.compile(r"\b([A-Z][A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|BEARER))(\s*[=:]\s*)[\"']?[^\s\"']{6,}[\"']?")


class BugsError(ValueError):
    """Bad input or an unusable store; the message is plain words and never holds a secret."""


def redact(text: str) -> str:
    text = _ASSIGNED.sub(lambda m: f"{m.group(1)}{m.group(2)}[hidden]", str(text or ""))
    for rx in _SECRETS:
        text = rx.sub("[hidden]", text)
    return text


def fingerprint(error_text: str) -> str:
    """The same error with different numbers, paths, hex ids or quoted values gives the same fingerprint."""
    text = str(error_text or "").lower().strip()
    if not text:
        return ""
    text = re.sub(r"\"[^\"]*\"|'[^']*'", "<q>", text)
    text = re.sub(r"(?:[a-z]:)?(?:[\\/][\w.@~-]+)+[\\/]?", "<path>", text)
    text = re.sub(r"\b(?:0x)?[0-9a-f]{8,}\b", "<hex>", text)
    text = re.sub(r"\d+", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    try:
        data = json.loads(BUGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise BugsError("the known-bugs file is unreadable; restore it before adding to it") from None
    if not isinstance(data, dict) or not isinstance(data.get("bugs"), dict):
        raise BugsError("the known-bugs file is malformed; restore it before adding to it")
    return data["bugs"]


def _save(bugs: dict) -> None:
    BUGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BUGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"bugs": bugs}, indent=1), encoding="utf-8")
    os.replace(tmp, BUGS_FILE)


def _clean(field: str, value) -> str:
    text = redact(str(value or "").strip())
    if len(text) > TEXT_LIMITS[field]:
        text = text[:TEXT_LIMITS[field] - 1] + "…"
    return text


def _summary(entry: dict, **extra) -> dict:
    out = {k: v for k, v in entry.items() if k != "occurrences"}
    out["occurrences"] = len(entry.get("occurrences") or [])
    return {**out, **extra}


@tool(name="report", category="bugs", doc="bugs/known.md")
def report(title: str, symptom: str = "", error_text: str = "", root_cause: str = "", workaround: str = "",
           fix: str = "", fix_status: str = "", component: str = "", guard: str = "", tags: list[str] | None = None,
           source: str = "claude", id: str = "", audience: str = "") -> dict:
    """Record a known bug, or a new occurrence of one already known (same error fingerprint), with its cause and fix.

    Args:
        title: short name of the bug (3-120 characters).
        symptom: what the person sees.
        error_text: the error message as printed; normalised into a fingerprint to recognise the bug next time.
        root_cause: why it happens.
        workaround: how to get past it today.
        fix: the permanent fix, if there is one.
        fix_status: "open", "workaround-only" or "fixed". Empty keeps the current one ("open" for a new bug).
        component: the part of the system (e.g. "guard-engine", "dashboard").
        guard: the guard id that now prevents it, if one does.
        tags: a few words to find it by.
        source: who reported it.
        id: update this known bug (e.g. "KB-0012") instead of matching by fingerprint; for marking a fix.
        audience: "public" puts the bug in the Pro known-bugs feed (bugs.export); "private" (the default for a
            new bug) keeps it on this install only.
    Returns:
        The stored bug (occurrences as a count). "matched": true when it was already known.
    """
    title = str(title or "").strip()
    if not 3 <= len(title) <= 120:
        raise BugsError("a title of 3 to 120 characters is needed")
    if fix_status and fix_status not in STATUSES:
        raise BugsError(f"fix_status must be one of: {', '.join(STATUSES)}")
    if audience and audience not in AUDIENCES:
        raise BugsError("audience must be public or private")
    fields = {f: _clean(f, v) for f, v in (("title", title), ("symptom", symptom), ("error_text", error_text),
              ("root_cause", root_cause), ("workaround", workaround), ("fix", fix), ("component", component),
              ("guard", guard))}
    tag_list = [_clean("component", t) for t in (tags or []) if str(t).strip()][:10]
    fp = fingerprint(error_text)
    with _lock:
        bugs = _load()
        if id and id not in bugs:
            raise BugsError(f"{id} isn't a known bug")
        known = bugs[id] if id else next((b for b in bugs.values() if fp and b.get("fingerprint") == fp), None)
        if known is not None:
            for f, v in fields.items():
                if v and f != "title":
                    known[f] = v
            if fix_status:
                known["fix_status"] = fix_status
            if audience:
                known["audience"] = audience
            known["tags"] = sorted(set(known.get("tags") or []) | set(tag_list))
            if not id:  # a report matched by its error is the bug happening again; an edit by id isn't
                known["occurrences"].append({"at": _now(), "by": _clean("component", source)})
            known["updated"] = _now()
            _save(bugs)
            return _summary(known, matched=True)
        number = max([int(k.split("-")[1]) for k in bugs if k.startswith("KB-")] + [0]) + 1
        entry = {"id": f"KB-{number:04d}", **fields, "fix_status": fix_status or "open", "tags": tag_list,
                 "audience": audience or "private",
                 "fingerprint": fp, "occurrences": [{"at": _now(), "by": _clean("component", source)}],
                 "created": _now(), "updated": _now()}
        bugs[entry["id"]] = entry
        _save(bugs)
        return _summary(entry, matched=False)


@tool(name="find", category="bugs", doc="bugs/known.md")
def find(query: str, k: int = 5) -> dict:
    """Has this been seen before? Paste an error message or describe the problem; get the known cause and fix.

    Args:
        query: an error message or a few words describing the problem.
        k: how many matches, 1 to 20.
    Returns:
        {"matches": [{id, title, fix_status, fix, workaround, root_cause, guard, score, occurrences}]}, best first.
        An identical error (same fingerprint) scores 100; otherwise shared words score 10 each.
    """
    if not 1 <= int(k) <= 20:
        raise BugsError("k must be 1 to 20")
    query = str(query or "").strip()
    if not query:
        raise BugsError("describe the problem or paste the error")
    fp, words = fingerprint(query), _words(query)
    matches, bugs = [], _load()
    if BUG_ID.fullmatch(query.upper()):  # "KB-0031", as a guard's block message names it: that bug itself
        b = bugs.get(query.upper())
        return {"matches": [{**{f: b.get(f, "") for f in ("id", "title", "fix_status", "fix", "workaround", "root_cause",
                                                          "guard")}, "score": 100,
                             "occurrences": len(b.get("occurrences") or [])}] if b else []}
    for b in bugs.values():
        if fp and b.get("fingerprint") == fp:
            score = 100
        else:
            text = " ".join(str(b.get(f) or "") for f in ("title", "symptom", "error_text", "root_cause"))
            score = 10 * len(words & _words(text + " " + " ".join(b.get("tags") or [])))
        if score:
            matches.append({**{f: b.get(f, "") for f in ("id", "title", "fix_status", "fix", "workaround", "root_cause",
                                                         "guard")}, "score": score, "occurrences": len(b.get("occurrences") or [])})
    matches.sort(key=lambda m: (-m["score"], m["id"]))
    # Semantic fallback (optional, needs Shedkeeper): with no exact
    # error and only weak word overlap, a paraphrased report of a known bug would be missed. Shedkeeper picks which of the
    # top candidates it is (or none of them) in well under a second; its pick moves to the top, marked matched_by.
    if matches and matches[0]["score"] < SHEDKEEPER_BELOW:
        picked = _shedkeeper_pick(query, matches[:SHEDKEEPER_CANDIDATES], bugs)
        if picked:
            bug_id, strength = picked
            for m in matches:
                if m["id"] == bug_id:
                    score = SHEDKEEPER_SCORE if strength == "strong" else SHEDKEEPER_POSSIBLE_SCORE
                    m["score"], m["matched_by"] = max(m["score"], score), "shedkeeper" if strength == "strong" else "shedkeeper-possible"
        matches.sort(key=lambda m: (-m["score"], m["id"]))
    result = {"matches": matches[:int(k)]}
    if not matches or matches[0]["score"] < SHEDKEEPER_BELOW:  # nothing known fits: maybe someone else has seen it
        outside = _outside(query)
        if outside:
            result["outside"] = outside
    return result


SHEDKEEPER_BELOW, SHEDKEEPER_CANDIDATES, SHEDKEEPER_PICK, SHEDKEEPER_SCORE = 30, 6, 0.6, 60
# A plain-words complaint sits far from a terse technical title, so "none of these" often edges ahead even when one
# bug clearly leads the others (live 2026-09-29: the right bug 0.28, the next 0.08, "none" 0.48). A clear leader is
# shown as a possible match, labelled so the reader judges it.
SHEDKEEPER_POSSIBLE_MIN, SHEDKEEPER_POSSIBLE_LEAD, SHEDKEEPER_POSSIBLE_SCORE = 0.2, 3.0, 45
# Words that match everything and so mean nothing: "the" and "for" alone once lifted a weak match to Shedkeeper's
# threshold, so Shedkeeper was never asked (found in the tests, 2026-09-29).
_STOP = frozenset("the and for with this that from are was were has have had not but you your its into than then "
                  "them they there when what which who why how all any can will just also out off one".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_.-]{3,}", str(text or "").lower()) if w not in _STOP}


def _shedkeeper_pick(query: str, candidates: list[dict], bugs: dict) -> tuple[str, str] | None:
    """(bug id, "strong" or "possible") for the candidate Shedkeeper thinks this problem is, or None (Shedkeeper missing or no
    clear answer). Candidates are described by title and symptom: closer to how people describe a problem."""
    try:
        from tools.local_ai import decide as decide_module
    except ImportError:  # the public copy has no Shedkeeper (a companion service)
        return None
    url = decide_module._shedkeeper_url()
    if not url or not candidates:
        return None
    criteria = {c["id"]: f"{bugs.get(c['id'], {}).get('title', c['title'])}. {bugs.get(c['id'], {}).get('symptom', '')}"
                .strip(" .")[:200] for c in candidates}
    criteria["none"] = "none of these: a different problem"
    try:
        out = decide_module._shedkeeper_choice(url, f"Problem: {query[:600]}", "Which known bug is this problem?", criteria)
    except decide_module.DecideError:
        return None
    probs = out["probabilities"]
    if out["choice"] != "none" and probs.get(out["choice"], 0) >= SHEDKEEPER_PICK:
        return out["choice"], "strong"
    ranked = sorted(((p, bid) for bid, p in probs.items() if bid != "none"), reverse=True)
    if ranked and ranked[0][0] >= SHEDKEEPER_POSSIBLE_MIN and (len(ranked) == 1 or ranked[0][0] >= SHEDKEEPER_POSSIBLE_LEAD * ranked[1][0]):
        return ranked[0][1], "possible"
    return None


COMPACT = ("id", "title", "fix_status", "component", "audience", "updated", "occurrences")


@tool(name="list", category="bugs", doc="bugs/known.md")
def list_bugs(fix_status: str = "", component: str = "", limit: int = 50, detail: str = "compact") -> dict:
    """Known bugs, newest first, optionally only one status or component.

    Args:
        fix_status: "open", "workaround-only" or "fixed" (empty = all).
        component: e.g. "guard-engine" (empty = all).
        limit: 1 to 100.
        detail: "compact" (the default: id, title, status, component, audience, last update and occurrences, about
            150 characters a bug) or "full" (every field). bugs.find gives the full text of the ones that matter;
            43 full bugs were ~30K characters (R&D's efficiency review, 2026-09-29).
    Returns:
        {"count", "bugs": [...]} with occurrences as a count.
    """
    if not 1 <= int(limit) <= 100:
        raise BugsError("limit must be 1 to 100")
    if fix_status and fix_status not in STATUSES:
        raise BugsError(f"fix_status must be one of: {', '.join(STATUSES)}")
    if detail not in ("compact", "full"):
        raise BugsError('detail must be "compact" or "full"')
    out = [_summary(b) for b in _load().values()
           if (not fix_status or b.get("fix_status") == fix_status) and (not component or b.get("component") == component)]
    out.sort(key=lambda b: b["id"], reverse=True)
    if detail == "compact":
        out = [{k: b[k] for k in COMPACT if k in b} for b in out]
    return {"count": len(out), "bugs": out[:int(limit)]}


# --- The shared feed. The owner marks a bug audience="public";
# bugs.export turns those into a known-bugs pack, cleaned of home-network details, for the Pro API (the same
# /pro-api/packs channel as rule packs). A Pro install's bugs.sync fetches it with its own Pro token and merges it into
# its local store as PRO-KB-..., where bugs.find (and Shedkeeper) search it. Nobody's errors ever leave their machine.
AUDIENCES = ("private", "public")
FEED_SLUG, FEED_MAX = "known-bugs", 2000
FEED_FIELDS = ("title", "symptom", "error_text", "root_cause", "workaround", "fix", "component", "guard")
_PRIVATE_IP = re.compile(r"\b(?:10(?:\.\d{1,3}){3}|127(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}"
                         r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b")
_WIN_USER = re.compile(r"(?i)\b([a-z]:[\\/]+users[\\/]+)[^\\/\s\"'`<]+")
_NIX_USER = re.compile(r"(/home/)[^/\s\"'`<]+")
_AT_HOST = re.compile(r"\b[\w.-]+@(?:[\w-]+\.)*[\w-]+\b")
_FEED_ID = re.compile(r"^KB-\d{4}$")


def sanitize(text: str) -> str:
    """A private bug report made fit to publish: secrets, private addresses, user folders and user@host hidden."""
    text = redact(text)
    text = _AT_HOST.sub("<user>@<host>", text)  # first: root@192.168.x.x is one target, not a user and an address
    text = _PRIVATE_IP.sub("<ip>", text)
    text = _WIN_USER.sub(lambda m: m.group(1) + "<you>", text)
    return _NIX_USER.sub(lambda m: m.group(1) + "<you>", text)


def export_pack() -> dict:
    """The public bugs as a known-bugs pack for the Pro API, every text field sanitized."""
    out = []
    for b in sorted(_load().values(), key=lambda b: b["id"]):
        if b.get("audience") != "public" or not _FEED_ID.match(b["id"]):
            continue
        out.append({"origin_id": b["id"], **{f: sanitize(b.get(f, "")) for f in FEED_FIELDS},
                    "fix_status": b.get("fix_status", "open"), "tags": [sanitize(t) for t in b.get("tags") or []],
                    "occurrences": len(b.get("occurrences") or [])})
    return {"formatVersion": 1, "kind": "known-bugs", "slug": FEED_SLUG, "title": "Known bugs and their fixes",
            "tier": "pro", "version": _now()[:10], "bugs": out}


def import_feed(pack) -> dict:
    """Merge a known-bugs pack into this store as PRO-KB-...: new bugs added, known ones refreshed. Every field is
    cleaned again on the way in (never trust a download)."""
    if not isinstance(pack, dict) or pack.get("kind") != "known-bugs" or not isinstance(pack.get("bugs"), list):
        raise BugsError("that isn't a known-bugs pack")
    if len(pack["bugs"]) > FEED_MAX:
        raise BugsError(f"the pack holds more than {FEED_MAX} bugs")
    added = updated = 0
    with _lock:
        bugs = _load()
        for item in pack["bugs"]:
            if not isinstance(item, dict) or not _FEED_ID.match(str(item.get("origin_id") or "")):
                continue
            fields = {f: _clean(f, item.get(f)) for f in FEED_FIELDS}
            if len(fields["title"]) < 3:
                continue
            status = item.get("fix_status") if item.get("fix_status") in STATUSES else "open"
            tags = [_clean("component", t) for t in (item.get("tags") or []) if str(t).strip()][:10]
            bid = "PRO-" + item["origin_id"]
            if bid in bugs:
                bugs[bid].update(fields, fix_status=status, tags=tags, fingerprint=fingerprint(fields["error_text"]),
                                 updated=_now())
                updated += 1
            else:
                bugs[bid] = {"id": bid, **fields, "fix_status": status, "tags": tags,
                             "fingerprint": fingerprint(fields["error_text"]), "occurrences": [], "audience": "pro",
                             "created": _now(), "updated": _now()}
                added += 1
        _save(bugs)
    return {"added": added, "updated": updated, "total": len(bugs)}


@tool(name="export", category="bugs", doc="bugs/known.md")
def export() -> dict:
    """The known-bugs feed: every bug marked audience "public", cleaned of secrets, private addresses,
    user folders and user@host. Publish its JSON as the "known-bugs" pack on the Pro API.

    Returns:
        {"formatVersion", "kind": "known-bugs", "slug", "title", "tier", "version", "bugs": [...]}.
    """
    return export_pack()


# --- Outside matches (opt-in). Scraping another
# tracker breaks its terms and copies other people's words, so instead: when nothing known fits, search public GitHub
# issues and return links only. Only a cleaned signature of the error leaves the machine (no paths, addresses,
# numbers, hex ids, quoted values or secrets), and only when the owner has switched it on (bugs_search_public).
OUTSIDE_TTL_S, OUTSIDE_MAX = 600, 3
_outside_cache: dict[str, tuple[float, list]] = {}


def signature(text: str) -> str:
    """The searchable words of an error, with everything private or incidental removed."""
    text = sanitize(str(text or "")).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\"[^\"]*\"|'[^']*'", " ", text)
    text = re.sub(r"(?:[a-z]:)?(?:[\\/][\w.@~<>-]+)+[\\/]?", " ", text)
    text = re.sub(r"\b(?:0x)?[0-9a-f]{8,}\b|\d+", " ", text)
    text = re.sub(r"<[a-z]+>|\[hidden\]", " ", text)
    words = [w for w in re.findall(r"[a-z_][a-z0-9_.-]{2,}", text) if w not in _STOP]
    return " ".join(words[:12])


def _outside(query: str) -> list[dict]:
    """Up to 3 similar public GitHub issues as links, or [] (switched off, too little to search on, or any failure:
    this must never break a lookup). Cached 10 minutes per signature."""
    import runtime_settings
    if not runtime_settings.get("bugs_search_public"):
        return []
    sig = signature(query)
    if len(sig.split()) < 2:
        return []
    now = time.time()
    cached = _outside_cache.get(sig)
    if cached and now - cached[0] < OUTSIDE_TTL_S:
        return cached[1]
    import httpx

    import vault
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "homelab-known-bugs"}
    token = vault.secret("GITHUB_SEARCH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = httpx.get("https://api.github.com/search/issues", headers=headers, timeout=6,
                      params={"q": f"{sig} is:issue", "per_page": OUTSIDE_MAX})
        items = r.json().get("items", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError, AttributeError):
        items = []
    out = [{"title": str(i.get("title", ""))[:160], "url": str(i.get("html_url", "")), "state": str(i.get("state", "")),
            "repo": "/".join(str(i.get("repository_url", "")).split("/")[-2:])}
           for i in (items if isinstance(items, list) else [])[:OUTSIDE_MAX] if isinstance(i, dict)]
    _outside_cache[sig] = (now, out)
    return out

