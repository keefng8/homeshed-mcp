"""schedule.*: run a read-only tool on a timetable (the owner, 2026-10-23: end-user tools that work). R&D's verdict
(researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md): APScheduler 3.x, the jobs kept in our own JSON."""

ZONES = ["UTC", "Europe/London", "Europe/Dublin", "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Rome",
         "Europe/Amsterdam", "Europe/Stockholm", "Europe/Athens", "America/New_York", "America/Chicago",
         "America/Denver", "America/Los_Angeles", "America/Toronto", "America/Sao_Paulo", "Asia/Dubai",
         "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland"]

SETTINGS = {
    "schedule_timezone": {
        "type": "select", "default": "UTC", "group": "Schedules", "options": ZONES,
        "label": "Time zone for schedules",
        "help": "\"Daily 08:00\" means 08:00 here. Clocks going forward or back are handled. Changing it moves "
                "every schedule's next run to the new zone.",
    },
}
