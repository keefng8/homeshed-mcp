# Observe — List

## ID
`observe.list`

## Purpose
Read the observation log. See `log.md` for what the log is and why it exists.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `status` | string | `"open"` | `open`, `actioned`, `declined`, `superseded`, `parked`, or `all`. |
| `rule` | string | `""` | Only observations tagged with this rule id. |
| `limit` | integer | `50` | Max entries returned. `count` is always the full match count. |
| `action_class` | string | `""` | Only this kind of slip, e.g. `shell.grep-command` (an observation logged before classes is `obs-<id>`). |

## Returns
`{count, observations: [{id, title, status, rule, class, area, date, source, stops, last_seen}]}`, oldest first.
`stops` and `last_seen` are the guard stops the daily self-review recorded for the class. It
returns frontmatter only. The full issue/fix text lives in the file on the `observations` volume.

## Errors
`ValueError` for an unknown `status`.

## Implementation
`mcp-server/tools/observe/list.py`, via `_store.list_all`.
