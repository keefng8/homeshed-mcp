# Schedule — add, list, remove, pause

## ID
`schedule.add` (risk `write`: it creates a schedule), `schedule.list` (risk `read`), `schedule.remove`,
`schedule.pause` (risk `write`)

## Purpose
Run a read-only tool on a timetable, unattended (the owner, 2026-10-23): a morning shop check, a weekly report, a
page checked every few hours. The AI no longer has to be asked each time. R&D's verdict
(`researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md`): APScheduler 3.x (MIT), the schedules kept
in HomeShed's own JSON.

## Rules
- **Read-only tools only.** A tool whose risk is `write` (it changes something real: posts, pays, deletes) is
  refused when the schedule is added, and checked again before every run. `workflow.run` is allowed when every one
  of its steps reads. A schedule can't run `schedule.*`.
- **Runs as its maker.** Each run happens as the client that made the schedule, so that client's grants and rate
  limit apply. The owner's schedules run as the owner. A client sees and changes only its own schedules.
- **At most every 15 minutes**, at most 50 schedules.
- **One run at a time** per schedule. Runs missed while the server was down are merged into one and run if under
  an hour late, else skipped to the next time.
- **3 failures in a row pause it**, with one high-priority phone alert. `schedule.pause(id, paused=false)` resumes it.
- **Only a summary is kept** (time, ok, milliseconds, length, a short error), the last 10 runs. Never the result.

## When
In the time zone under Control Panel > Settings > Schedules (default UTC; changing it moves every schedule).
| Say | Means |
|---|---|
| `daily 08:00` | every day at 08:00 |
| `weekdays 07:30`, `weekends 10:00` | Monday to Friday, Saturday and Sunday |
| `weekly mon 09:00` | every Monday |
| `monthly 1 09:00` | the 1st of the month (days 1 to 28) |
| `hourly` | on the hour |
| `every 30 minutes`, `every 2 hours` | from when it was added |
| `0 8 * * 1-5` | cron: minute hour day month weekday |

## Alerts (`notify`)
`on_change` (default): a phone alert (notify.send) when the result differs from the last run's. `always`, `on_error`
or `never`, or `when_flagged` (the result itself says `"alert": true`, with a `summary`; web.watch uses it). The alert carries the first 700 characters of the result.

## Files
`usage/schedules.json` (env `SCHEDULES_FILE`). Started once at server start-up (`tools/schedule/engine.start`).
