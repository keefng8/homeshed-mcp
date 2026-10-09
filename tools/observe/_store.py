"""File-backed observation log shared by observe.log / observe.list / observe.update and the
/observations/summary and /observations/repeat routes. See ../../capabilities/observe/log.md.

One markdown file per observation (`NNNN-slug.md`) with a flat `key: value` frontmatter block,
format adapted from reference-repos/one-skill-to-rule-them-all (task-observer, Eoghan Henn,
CC BY 4.0). Plain files on a Docker volume, no database: easy to read, back up and grep.

Repeats key on the action class, the kind of slip (R&D's rules review v2, 2026-10-02): the free-text `rule` never met
its own count (one slip was filed under three rule ids, others under a title or "15"). The rule is kept only as a
label, and only when it looks like a rule id. An observation without a class joins one whose words it shares (3-word
Jaccard 0.6 or more), else it's its own. Guard stops reach the count through record_repeat (the daily self-review), so
RULE-D-PROJECTS-007 fires without anyone logging the slip, and an "actioned" item hit again says its fix didn't hold.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from paths import data_path

STATUSES = ("open", "actioned", "declined", "superseded", "parked")
# Words sessions reach for (6 failed observe.update calls in 3 days, 2026-10-02): taken as the status they mean.
STATUS_WORDS = {"fixed": "actioned", "done": "actioned", "resolved": "actioned", "closed": "actioned",
                "wontfix": "declined", "rejected": "declined", "duplicate": "superseded"}
_FIELDS = ("id", "title", "status", "rule", "class", "area", "date", "source", "stops", "last_seen")
_FILE_RE = re.compile(r"^(\d{4})-[a-z0-9-]*\.md$")
RULE_ID = re.compile(r"^(?:RULE|CORE)-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
CLASS_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,60}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAME_WORDS = 0.6
ESCALATION_HINT = (
    "RULE-D-PROJECTS-007: this rule has been broken before. Don't reword it again; add a "
    "mechanical guard (a hook, a test, or a capability-level check) and record it as the fix."
)
BYPASS_HINT = ("The fix recorded on #{ids} didn't hold: this is the same kind of slip again. Make its guard cover the "
               "whole class (one deny for every variant) instead of adding another variant pattern (RULE-D-PROJECTS-007).")


def _dir() -> Path:
    path = Path(os.environ.get("OBSERVATIONS_DIR") or data_path("observations"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "observation"


def _parse(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        header, _, body = text[4:].partition("\n---\n")
        for line in header.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()
    return meta, body


def _write(path: Path, meta: dict, body: str) -> None:
    header = "\n".join(f"{k}: {meta.get(k, '')}" for k in _FIELDS)
    path.write_text(f"---\n{header}\n---\n{body}", encoding="utf-8")


def _files() -> list[Path]:
    return sorted(p for p in _dir().iterdir() if _FILE_RE.match(p.name))


def _all() -> list[dict]:
    out = []
    for path in _files():
        meta, _ = _parse(path)
        out.append({k: meta.get(k, "") for k in _FIELDS})
    return out


def _class(o: dict) -> str:
    """An observation's class: its own field, or its id for one logged before classes (it can still be joined)."""
    return o.get("class") or f"obs-{o.get('id')}"


def _shingles(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)} or ({tuple(words)} if words else set())


def _issue(body: str) -> str:
    return body.split("## Issue", 1)[-1].split("\n## ", 1)[0]


def _new_path(title: str) -> tuple[str, Path]:
    next_num = max((int(_FILE_RE.match(p.name).group(1)) for p in _files()), default=0) + 1
    while True:  # O_EXCL claim: two concurrent callers can never end up with the same id
        obs_id = f"{next_num:04d}"
        path = _dir() / f"{obs_id}-{_slug(title)}.md"
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return obs_id, path
        except FileExistsError:
            next_num += 1


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def create(title: str, issue: str, rule: str = "", fix: str = "", area: str = "",
           source: str = "claude", action_class: str = "") -> dict:
    title, rule, area, source, action_class = map(_one_line, (title, rule, area, source, action_class))
    if not title or not str(issue).strip():
        raise ValueError("title and issue are both required and must be non-empty")
    if action_class and not CLASS_RE.match(action_class):
        raise ValueError("action_class: lowercase words joined by dots or dashes, e.g. shell.grep-command")

    existing = []
    for path in _files():
        meta, body = _parse(path)
        if meta.get("status") != "declined":
            existing.append({**{k: meta.get(k, "") for k in _FIELDS},
                             "_words": _shingles(f"{meta.get('title')} {_issue(body)}")})
    cls = action_class
    if not cls:  # the same words as an earlier observation: the same slip (R&D's review: 3-word Jaccard 0.6+)
        mine = _shingles(f"{title} {issue}")
        score = lambda o: len(mine & o["_words"]) / len(mine | o["_words"]) if mine and o["_words"] else 0.0
        best = max(existing, key=score, default=None)
        cls = _class(best) if best is not None and score(best) >= SAME_WORDS else ""
    same = [o for o in existing if cls and _class(o) == cls]
    repeat_count = len(same) + sum(int(o.get("stops") or 0) for o in same)
    bypassed = [o["id"] for o in same if o["status"] == "actioned"]

    body = f"\n## Issue\n{str(issue).strip()}\n"
    if str(fix).strip():
        body += f"\n## Proposed fix\n{str(fix).strip()}\n"
    if rule and not RULE_ID.match(rule):
        body += f"\n## Rule given\n{rule} (not a rule id: kept here as written, never used to count repeats)\n"
        rule = ""
    obs_id, path = _new_path(title)
    meta = {"id": obs_id, "title": title, "status": "open", "rule": rule, "class": cls or f"obs-{obs_id}",
            "area": area, "date": _today(), "source": source}
    _write(path, meta, body)
    escalate = repeat_count >= 1
    hint = BYPASS_HINT.format(ids=", #".join(bypassed)) if bypassed else ESCALATION_HINT if escalate else None
    return {"id": obs_id, "file": path.name, "status": "open", "class": meta["class"], "repeat_count": repeat_count,
            "escalate": escalate, "bypassed": bypassed, "hint": hint}


def record_repeat(action_class: str, date: str, stops: int, sessions: int = 0, retries: int = 0,
                  guards: list | tuple = ()) -> dict:
    """One day of guard stops for a class (the daily self-review, POST /observations/repeat): kept on that class's
    observation, which is made when there's none yet. Idempotent per class and day. An actioned item whose stops are
    mostly retries reopens: its block message doesn't teach."""
    if not CLASS_RE.match(str(action_class or "")) or not DATE_RE.match(str(date or "")):
        raise ValueError("action_class and date (YYYY-MM-DD) are required")
    stops, sessions, retries = int(stops), int(sessions), int(retries)
    if stops < 1 or sessions < 0 or not 0 <= retries <= stops:
        raise ValueError("stops must be 1 or more, and retries between 0 and stops")
    guards = ", ".join(sorted({_one_line(g)[:60] for g in guards or () if str(g).strip()}))[:300]
    line = f"- {date}: stopped {stops} times in {sessions} session(s), {retries} retried" + (f" ({guards})" if guards else "")
    target = None
    for path in reversed(_files()):
        meta, body = _parse(path)
        if meta.get("class") == action_class and meta.get("status") != "declined":
            target = (path, meta, body)
            break
    if target is None:
        title = f"{action_class}: stopped {stops} times on {date}"
        obs_id, path = _new_path(title)
        meta = {"id": obs_id, "title": title, "status": "open", "rule": "", "class": action_class, "area": "guards",
                "date": _today(), "source": "self-review", "stops": str(stops), "last_seen": date}
        _write(path, meta, "\n## Issue\nGuard stops of this kind of slip, counted by the daily self-review: nobody had to "
                           f"log them.\n\n## Repeats\n{line}\n\n## Proposed fix\nThe guard holds. If most stops are "
                           "retries, reword its next call; if they keep rising, find what invites the slip.\n")
        return {"id": obs_id, "created": True, "status": "open", "stops": stops}
    path, meta, body = target
    if f"- {date}:" in body:
        return {"id": meta.get("id"), "created": False, "unchanged": True, "status": meta.get("status")}
    if "\n## Repeats\n" in body:
        head, _, rest = body.partition("\n## Repeats\n")
        section, sep, tail = rest.partition("\n## ")
        body = f"{head}\n## Repeats\n{section.rstrip()}\n{line}\n" + (f"\n## {tail}" if sep else "")
    else:
        body = body.rstrip("\n") + f"\n\n## Repeats\n{line}\n"
    meta["stops"] = str(int(meta.get("stops") or 0) + stops)
    meta["last_seen"] = date
    reopened = meta.get("status") == "actioned" and retries * 2 >= stops
    if reopened:
        meta["status"] = "open"
        body = body.rstrip("\n") + (f"\n\n## Reopened ({date})\n{retries} of {stops} stops were retries: the block "
                                    "message doesn't teach. Reword its next call.\n")
    _write(path, meta, body)
    return {"id": meta.get("id"), "created": False, "status": meta["status"], "stops": int(meta["stops"]),
            "reopened": reopened}


def list_all(status: str = "open", rule: str = "", limit: int = 50, action_class: str = "") -> dict:
    status = STATUS_WORDS.get(str(status).strip().lower(), status)
    if status != "all" and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES + ('all',)}")
    matches = [o for o in _all()
               if (status == "all" or o["status"] == status) and (not rule or o["rule"] == rule)
               and (not action_class or _class(o) == action_class)]
    return {"count": len(matches), "observations": matches[: max(1, limit)]}


def update(obs_id: str, status: str, resolution: str = "") -> dict:
    status = STATUS_WORDS.get(str(status).strip().lower(), str(status).strip().lower())
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    obs_id = str(obs_id).zfill(4)
    for path in _files():
        if path.name.startswith(f"{obs_id}-"):
            meta, body = _parse(path)
            previous = meta.get("status", "")
            meta["status"] = status
            if str(resolution).strip():
                body = body.rstrip("\n") + f"\n\n## Resolution ({status}, {_today()})\n{str(resolution).strip()}\n"
            _write(path, meta, body)
            return {"id": obs_id, "status": status, "previous_status": previous}
    raise ValueError(f"no observation with id '{obs_id}'")


def summary() -> dict:
    items = _all()
    by_status: dict[str, int] = {}
    for o in items:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
    open_items = [o for o in items if o["status"] == "open"]
    return {"open": len(open_items), "by_status": by_status,
            "open_titles": [o["title"] for o in open_items[:5]]}
