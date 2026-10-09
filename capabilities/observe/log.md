# Observe — Log

## ID
`observe.log` (with `observe.list` and `observe.update`, same store)

## Purpose
A persistent log of rule violations, user corrections and improvement gaps. Rules written as text
get broken through habit; this log makes each slip a tracked item with a status, so "is this fix
actually holding?" has something concrete to check against. The file format and the
"second violation → mechanical guard" principle are adapted from task-observer by Eoghan Henn
(CC BY 4.0).

## When to call it
- The user corrects how something was done ("falling back to habit", "you forgot the rule").
- The AI notices it broke one of the project's rules, whether or not anyone said so.
- A real gap turns up that shouldn't derail the current task (log it and move on).

Set `action_class` when you know the kind of slip (a guard's class, e.g. `shell.grep-command`): repeat
detection keys on it. Leave it empty and an observation whose words match an earlier one (3-word Jaccard 0.6 or
more) joins that one's class; otherwise it starts its own. `rule` is a label only: one free-text field never met its
own count (one slip was filed under three rule ids, others under a title or "15"; R&D's rules review, 2026-10-02).

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `title` | string | — | Required. One line. |
| `issue` | string | — | Required. What happened, observed vs expected. |
| `rule` | string | `""` | The rule id being broken, e.g. `RULE-MYAPP-006`. A label: anything that isn't a rule id (`RULE-...` or `CORE-...`) is kept in the body under "Rule given" instead. |
| `fix` | string | `""` | The proposed improvement, if known. |
| `area` | string | `""` | Free-form tag (`hooks`, `delegation`, `tests`…). |
| `source` | string | `"claude"` | Who logged it. |
| `action_class` | string | `""` | The kind of slip, lowercase with dots or dashes (`shell.inline-code-backslash`). What repeats are counted by. |

## Returns
`{id, file, status: "open", class, repeat_count, escalate, bypassed, hint}`. `repeat_count` counts earlier
observations of the same class that weren't declined, plus the guard stops the daily self-review recorded for it, so
a slip the guards keep catching escalates without anyone having logged it. Actioned ones **do** count, and are listed
in `bypassed`: a slip back after its fix landed means the fix didn't hold. When `escalate` is true, `hint` says to stop
rewording the rule and add a hook, test or tool-level check (or, when bypassed, to make the guard cover the whole
class) instead.

## Storage
One `NNNN-slug.md` file per observation, with a flat frontmatter block (`id, title, status,
rule, class, area, date, source, stops, last_seen`) and `## Issue` / `## Proposed fix` / `## Resolution` sections. It
lives in `OBSERVATIONS_DIR` (default `DATA_DIR/observations`). Ids are claimed with `O_EXCL`, so
concurrent calls can't collide. Newlines in one-line fields are collapsed, so a crafted title can't
inject frontmatter.

## Surfacing
`GET /observations/summary` (protected like `/capabilities`) returns `{open, by_status, open_titles}`.
A Claude Code `SessionStart` hook can read it and show the open count and titles at the start of each
session. Review happens with `observe.list` plus `observe.update`.

`POST /observations/repeat` (same protection) takes one day of guard stops for one class: `{class, date, stops,
sessions, retries, guards}`, counts and ids only. The daily self-review (`.claude/hooks/self_review.py`) posts the
classes stopped 5 or more times. It keeps a dated line under `## Repeats` on the class's observation (made when there's
none, `source: self-review`), once per class and day. An actioned one reopens when half or more of the day's stops were
retries: its block message doesn't teach.

## Implementation
`tools/observe/_store.py` (shared), `log.py`, `list.py`, `update.py`. Tests: `tests/test_observe.py`.

## Related
`observe.list` (`list.md`), `observe.update` (`update.md`).
