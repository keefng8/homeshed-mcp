"""schedule.add, schedule.list, schedule.remove, schedule.pause. See ../../capabilities/schedule/schedule.md."""
from __future__ import annotations

import clients
from registry import tool
from tools.schedule import engine


@tool(name="add", category="schedule", doc="schedule/schedule.md")
def add(name: str, capability: str, when: str, arguments: dict | None = None, notify: str = "on_change") -> dict:
    """Run a read-only tool on a timetable, unattended: a morning check, a weekly report, a page watched for changes.
    Only tools that read can be scheduled (a tool that changes something real is refused), and each run happens as
    whoever made the schedule, with the same permissions. At most every 15 minutes. 3 failures in a row pause it, with
    one phone alert.

    Args:
        name: what it's for, e.g. "Morning shop check" (shown in alerts).
        capability: the tool to run, e.g. "etsy.shop.get" or "web.read" (registry.find names it).
        when: plain words, in the time zone set under Settings > Schedules: "daily 08:00", "weekdays 07:30",
            "weekends 10:00", "weekly mon 09:00", "monthly 1 09:00", "hourly", "every 30 minutes", "every 2 hours";
            or a cron line "minute hour day month weekday", e.g. "0 8 * * 1-5".
        arguments: that tool's own arguments, as you'd pass them in a normal call.
        notify: when to send a phone alert with the result: "on_change" (default: the result differs from the last
            run), "always", "on_error" or "never".

    Returns:
        the schedule: {id, name, capability, arguments, when, notify, paused, next_run, ...}.
    """
    engine.follow_timezone()
    return engine.add(name, capability, arguments, when, notify, clients.current_client.get())


@tool(name="list", category="schedule", doc="schedule/schedule.md")
def list_schedules() -> dict:
    """Your schedules (the owner sees every app's): each with its next run, last run, whether it worked, and the last 5
    runs (time, ok, length, a short error; never the result itself).

    Returns:
        {"schedules": [...], "timezone", "count", "max"}
    """
    engine.follow_timezone()
    rows = engine.listing(clients.current_client.get())
    return {"schedules": rows, "timezone": engine.timezone(), "count": len(rows), "max": engine.MAX_SCHEDULES}


@tool(name="remove", category="schedule", doc="schedule/schedule.md")
def remove(schedule_id: str) -> dict:
    """Delete one of your schedules. It stops at once.

    Args:
        schedule_id: its id from schedule.list (e.g. "s-1a2b3c4d").
    """
    return engine.remove(schedule_id, clients.current_client.get())


@tool(name="pause", category="schedule", doc="schedule/schedule.md")
def pause(schedule_id: str, paused: bool = True) -> dict:
    """Pause a schedule, or start it again with paused=false (which also clears a pause after repeated failures).

    Args:
        schedule_id: its id from schedule.list.
        paused: true to pause, false to resume.
    """
    engine.follow_timezone()
    return engine.pause(schedule_id, paused, clients.current_client.get())
