"""The schedule store and runner behind schedule.*. See ../../capabilities/schedule/schedule.md.

A schedule is {id, name, capability, arguments, when, owner, notify, paused, ...}, kept in usage/schedules.json (the
source of truth; APScheduler holds only the triggers, in memory, rebuilt at start-up). Rules (R&D, 2026-10-24):
- Only read-risk tools run on a schedule. A write tool (one that changes something real) is refused when it's added,
  so nothing changes the world unattended; a workflow.run is allowed only when every one of its steps reads.
- A run happens as the client that made the schedule, so that client's grants and rate limit still apply.
- One run at a time per schedule (max_instances=1); missed runs while the server was down are merged into one
  (coalesce) and run if under MISFIRE_S late, else skipped to the next time.
- Every 15 minutes at most. 3 failures in a row pause it and send one phone alert, never one per failure.
- Only a summary of each run is kept (ok, time, length, a short error): never the result itself.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

from paths import data_path

MAX_SCHEDULES = 50
MIN_INTERVAL_MIN = 15
MISFIRE_S = 3600
FAILS_TO_PAUSE = 3
HISTORY = 10
NOTIFY = ("never", "always", "on_change", "on_error", "when_flagged")  # when_flagged: the result says "alert": true
FILE_DEFAULT = "usage/schedules.json"
NOT_SCHEDULABLE = {"schedule"}  # categories: a schedule never makes or changes schedules
_DAYS = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri", "sat": "sat", "sun": "sun"}
_lock = threading.RLock()
_sched = None  # the running APScheduler BackgroundScheduler, once start() ran
log = logging.getLogger(__name__)


class ScheduleError(ValueError):
    """Safe to show."""


def _file() -> Path:
    return Path(os.environ.get("SCHEDULES_FILE") or data_path(FILE_DEFAULT))


def load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("schedules"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"schedules": {}}


def _save(data: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def timezone() -> str:
    try:
        import runtime_settings
        return str(runtime_settings.get("schedule_timezone") or "UTC")
    except Exception:  # noqa: BLE001 - an unreadable store means the default
        return "UTC"


# --- "when": plain words or a 5-field cron line ---------------------------------------------------------------------

_HHMM = r"([01]?\d|2[0-3]):([0-5]\d)"


def parse_when(when: str) -> dict:
    """Plain words or cron, as trigger fields: {"kind": "cron", ...cron fields} or {"kind": "interval", "minutes": n}.
    Accepts: "every 30 minutes", "every 2 hours", "hourly", "daily 08:00", "weekdays 08:00", "weekends 10:00",
    "weekly mon 09:00", "monthly 1 09:00", or cron "m h dom mon dow" (e.g. "0 8 * * 1-5")."""
    w = re.sub(r"\s+", " ", str(when or "").strip().lower())
    if not w:
        raise ScheduleError('when is empty: e.g. "daily 08:00" or "every 2 hours"')
    if m := re.fullmatch(r"every (\d+) (minute|minutes|min|mins|hour|hours)", w):
        n = int(m.group(1)) * (60 if m.group(2).startswith("hour") else 1)
        if n < MIN_INTERVAL_MIN:
            raise ScheduleError(f"every {MIN_INTERVAL_MIN} minutes at most often")
        if n > 7 * 24 * 60:
            raise ScheduleError("an interval over a week: use weekly or monthly instead")
        return {"kind": "interval", "minutes": n}
    if w == "hourly":
        return {"kind": "cron", "minute": "0", "hour": "*", "day": "*", "month": "*", "day_of_week": "*"}
    if m := re.fullmatch(rf"(daily|weekdays|weekends) {_HHMM}", w):
        dow = {"daily": "*", "weekdays": "mon-fri", "weekends": "sat,sun"}[m.group(1)]
        return {"kind": "cron", "minute": str(int(m.group(3))), "hour": str(int(m.group(2))), "day": "*",
                "month": "*", "day_of_week": dow}
    if m := re.fullmatch(rf"weekly ({'|'.join(_DAYS)})[a-z]* {_HHMM}", w):
        return {"kind": "cron", "minute": str(int(m.group(3))), "hour": str(int(m.group(2))), "day": "*",
                "month": "*", "day_of_week": _DAYS[m.group(1)]}
    if m := re.fullmatch(rf"monthly ([1-9]|1\d|2[0-8]) {_HHMM}", w):
        return {"kind": "cron", "minute": str(int(m.group(3))), "hour": str(int(m.group(2))), "day": m.group(1),
                "month": "*", "day_of_week": "*"}
    parts = w.split(" ")
    if len(parts) == 5 and all(re.fullmatch(r"[\d*,/\-a-z]+", p) for p in parts):
        minute = parts[0]
        if minute == "*" or re.fullmatch(r"\*/(\d+)", minute) and int(minute[2:]) < MIN_INTERVAL_MIN:
            raise ScheduleError(f"every {MIN_INTERVAL_MIN} minutes at most often")
        fields = {"kind": "cron", "minute": minute, "hour": parts[1], "day": parts[2], "month": parts[3],
                  "day_of_week": parts[4]}
        _trigger(fields, "UTC")  # raises on a bad field
        return fields
    raise ScheduleError(f'{when!r} isn\'t a time this understands: e.g. "daily 08:00", "weekdays 07:30", '
                        '"weekly mon 09:00", "every 2 hours", or cron "0 8 * * 1-5"')


def _trigger(fields: dict, tz: str):
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    if fields["kind"] == "interval":
        return IntervalTrigger(minutes=fields["minutes"], timezone=tz)
    try:
        return CronTrigger(minute=fields["minute"], hour=fields["hour"], day=fields["day"], month=fields["month"],
                           day_of_week=fields["day_of_week"], timezone=tz)
    except ValueError as exc:
        raise ScheduleError(f"that cron line doesn't work: {exc}") from None


# --- what may run ---------------------------------------------------------------------------------------------------

def check_capability(capability: str, arguments: dict) -> None:
    """Refuse anything that isn't a read-risk tool (a workflow.run only when all its steps read)."""
    from registry import get_capability, tool_meta
    if get_capability(capability) is None:
        raise ScheduleError(f"no tool called {capability!r}: registry.find can name the right one")
    risk, category = tool_meta(capability)
    if category in NOT_SCHEDULABLE:
        raise ScheduleError("a schedule can't run schedule tools")
    if capability == "workflow.run":
        steps = arguments.get("steps") if isinstance(arguments, dict) else None
        if not isinstance(steps, list) or not steps:
            raise ScheduleError("workflow.run needs its steps in arguments")
        for i, s in enumerate(steps):
            cap = s.get("capability") if isinstance(s, dict) else None
            if not isinstance(cap, str) or cap == "workflow.run":
                raise ScheduleError(f"workflow step {i} needs a capability (and can't be another workflow.run)")
            check_capability(cap, s.get("arguments") or {})
        return
    if risk != "read":
        raise ScheduleError(f"{capability} changes something real, so it can't run unattended on a schedule; "
                            "schedules run read-only tools")


# --- add, list, change ----------------------------------------------------------------------------------------------

def _visible(s: dict, client: str | None) -> bool:
    return client is None or s.get("owner") == client  # the owner sees all; a client its own


def public(s: dict) -> dict:
    out = {k: s.get(k) for k in ("id", "name", "capability", "arguments", "when", "notify", "paused", "paused_why",
                                 "owner", "created_at", "last_run", "last_ok", "fails_in_a_row", "runs")}
    out["history"] = (s.get("history") or [])[-5:]
    out["next_run"] = next_run(s["id"])
    return out


def add(name: str, capability: str, arguments: dict | None, when: str, notify: str, client: str | None) -> dict:
    name = str(name or "").strip()[:80]
    if not name:
        raise ScheduleError("name is empty: say what it's for, e.g. \"Morning shop check\"")
    if notify not in NOTIFY:
        raise ScheduleError(f"notify must be one of {', '.join(NOTIFY)}")
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        raise ScheduleError("arguments must be an object of the tool's own arguments")
    if len(json.dumps(arguments)) > 20_000:
        raise ScheduleError("arguments are over 20,000 characters")
    fields = parse_when(when)
    check_capability(capability, arguments)
    with _lock:
        data = load()
        if len(data["schedules"]) >= MAX_SCHEDULES:
            raise ScheduleError(f"there are {MAX_SCHEDULES} schedules already: remove one first")
        sid = "s-" + secrets.token_hex(4)
        s = {"id": sid, "name": name, "capability": capability, "arguments": arguments, "when": when.strip(),
             "fields": fields, "notify": notify, "paused": False, "owner": client,
             "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "runs": 0, "fails_in_a_row": 0, "history": []}
        data["schedules"][sid] = s
        _save(data)
        _arm(s)
        return public(s)


def get(sid: str, client: str | None) -> dict:
    s = load()["schedules"].get(str(sid))
    if s is None or not _visible(s, client):
        raise ScheduleError(f"no schedule {sid!r} (schedule.list shows them)")
    return s


def listing(client: str | None) -> list[dict]:
    return [public(s) for s in load()["schedules"].values() if _visible(s, client)]


def remove(sid: str, client: str | None) -> dict:
    with _lock:
        s = get(sid, client)
        data = load()
        data["schedules"].pop(s["id"], None)
        _save(data)
        _disarm(s["id"])
    return {"removed": s["id"], "name": s["name"]}


def pause(sid: str, paused: bool, client: str | None) -> dict:
    with _lock:
        s = get(sid, client)
        data = load()
        s = data["schedules"][s["id"]]
        s["paused"] = bool(paused)
        if not paused:
            s["fails_in_a_row"], s["paused_why"] = 0, None
        _save(data)
        _disarm(s["id"]) if paused else _arm(s)
    return public(s)


# --- running --------------------------------------------------------------------------------------------------------

def _digest(result) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _alert(title: str, message: str, priority: str = "default") -> None:
    try:
        from tools.notify.send import send
        send(message=message[:900], title=title[:80], priority=priority, tags=["alarm_clock"])
    except Exception as exc:  # noqa: BLE001 - no phone set up is fine; the run is still recorded
        log.info("schedule alert not sent: %s", exc)


def run_one(sid: str) -> dict | None:
    """One run, as the schedule's owner. Records a summary; alerts per the notify choice. Never raises."""
    import clients
    from registry import get_capability
    s = load()["schedules"].get(sid)
    if s is None or s.get("paused"):
        return None
    cap = get_capability(s["capability"])
    start, ok, err, digest, size = time.monotonic(), False, None, None, 0
    tok_c, tok_t = clients.current_client.set(s.get("owner")), clients.current_tool.set(None)
    try:
        check_capability(s["capability"], s.get("arguments") or {})  # a tool can turn riskier after it was added
        result = cap.func(**(s.get("arguments") or {}))
        ok, digest, size = True, _digest(result), len(json.dumps(result, default=str))
    except Exception as exc:  # noqa: BLE001 - a failed run is recorded, not raised
        err = str(exc)[:200] or type(exc).__name__
        result = None
    finally:
        clients.current_client.reset(tok_c)
        clients.current_tool.reset(tok_t)
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ok": ok, "ms": round((time.monotonic() - start) * 1000),
             "chars": size, **({"error": err} if err else {})}
    with _lock:
        data = load()
        s = data["schedules"].get(sid)
        if s is None:
            return entry
        changed = ok and s.get("last_digest") not in (None, digest)
        s["runs"] = int(s.get("runs") or 0) + 1
        s["last_run"], s["last_ok"] = entry["at"], ok
        s["history"] = (s.get("history") or [])[-(HISTORY - 1):] + [entry]
        if ok:
            s["fails_in_a_row"], s["last_digest"] = 0, digest
        else:
            s["fails_in_a_row"] = int(s.get("fails_in_a_row") or 0) + 1
            if s["fails_in_a_row"] >= FAILS_TO_PAUSE:
                s["paused"], s["paused_why"] = True, f"{FAILS_TO_PAUSE} failures in a row; last: {err}"
        _save(data)
        paused_now = s.get("paused")
    if paused_now:
        _disarm(sid)
        _alert(f"Schedule paused: {s['name']}", f"{s['paused_why']}. Fix it, then schedule.pause(id, paused=false).",
               "high")
        return entry
    n = s.get("notify")
    if n == "when_flagged":
        if ok and isinstance(result, dict) and result.get("alert") is True:
            _alert(s["name"], str(result.get("summary") or json.dumps(result, default=str))[:700])
        elif not ok:
            _alert(f"{s['name']}: check failed", err or "failed")
        return entry
    if n == "always" or (n == "on_error" and not ok) or (n == "on_change" and changed):
        body = (json.dumps(result, default=str)[:700] if ok else f"failed: {err}")
        _alert(f"{s['name']}" + (" changed" if changed and n == "on_change" else ""), body)
    return entry


# --- the scheduler --------------------------------------------------------------------------------------------------

_armed_tz: str | None = None


def follow_timezone() -> None:
    """The owner changed the time zone in Settings: re-arm everything in the new one (checked on every schedule call)."""
    if _sched is not None and timezone() != _armed_tz:
        rearm_all()


def _arm(s: dict) -> None:
    if _sched is None or s.get("paused"):
        return
    _sched.add_job(run_one, _trigger(s["fields"], timezone()), args=[s["id"]], id=s["id"], replace_existing=True,
                   max_instances=1, coalesce=True, misfire_grace_time=MISFIRE_S)


def _disarm(sid: str) -> None:
    if _sched is not None and _sched.get_job(sid) is not None:
        _sched.remove_job(sid)


def next_run(sid: str) -> str | None:
    job = _sched.get_job(sid) if _sched is not None else None
    t = getattr(job, "next_run_time", None)
    return t.isoformat(timespec="minutes") if t else None


def rearm_all() -> None:
    """Every schedule's trigger again (start-up, or the owner changed the time zone)."""
    global _armed_tz
    _armed_tz = timezone()
    for s in load()["schedules"].values():
        _disarm(s["id"])
        _arm(s)


def start() -> None:
    """Once, at server start-up, in the one server process (R&D: never in several workers)."""
    global _sched
    if _sched is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler
    _sched = BackgroundScheduler(timezone=timezone(), job_defaults={"max_instances": 1, "coalesce": True})
    _sched.start()
    rearm_all()
    log.info("schedules: %d armed", len(_sched.get_jobs()))


def stop() -> None:
    global _sched
    if _sched is not None:
        _sched.shutdown(wait=False)
        _sched = None
