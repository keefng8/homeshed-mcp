# Web — Watch

## ID
`web.watch` (risk `write`: it makes a schedule), `web.watch.check` (risk `read`)

## Purpose
A phone alert when a web page changes: a price drops, something is back in stock, a new post appears (the owner,
2026-10-23). R&D's verdict (`researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md`): built on
`web.read` and Python's difflib, our own code.

## How it works
`web.watch` does a first look at the page (so a bad address or pattern fails at once), then makes a schedule
(`schedule.*`, alert mode `when_flagged`) that runs `web.watch.check` on the timetable. Each check reads the page,
drops the lines matching `ignore`, and compares the rest, line by line, with the last look:
- **Two checks must agree.** A new version is held until the next check sees the same thing, so one odd load
  (an advert, a half-loaded page) never alerts. The alert comes one check after the change.
- **Big enough:** added plus removed characters must reach `min_change_chars` (default 20).
- **Trigger text:** with `trigger` set (e.g. `In stock`), it alerts only when that text newly appears, whatever
  else changes.

`schedule.list` shows the watch (its next run, last runs); `schedule.remove` stops it. A failed check alerts too,
and 3 in a row pause it (the schedule rules).

## Parameters (`web.watch`)
| Name | Default | Description |
|---|---|---|
| `url` | — | The public page (`web.read`'s rules: no local or private addresses). |
| `every` | `every 2 hours` | As `schedule.add` takes it; every 15 minutes at most often. |
| `name` | `Watch: <page>` | Shown in alerts. |
| `ignore` | `""` | Pattern for noisy lines to leave out, e.g. `updated|\d+ views|min ago`. |
| `trigger` | `""` | Text whose appearance is the only thing that alerts. |
| `min_change_chars` | `20` | Smaller changes are ignored. |

## Returns
`web.watch`: the schedule, plus `first_look`. `web.watch.check`: `{url, status: first_look|same|changed_once|changed,
alert, summary, added, removed}` (up to 8 lines each).

## Notes
Pages are read through `web.read` (Jina Reader), so the page address leaves the machine, as with `web.read`.
Up to 100 watched pages; the last text of each is kept in `usage/web_watch.json` (env `WEB_WATCH_FILE`).
