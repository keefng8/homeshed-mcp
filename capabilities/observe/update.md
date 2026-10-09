# Observe — Update

## ID
`observe.update`

## Purpose
Move an observation through its lifecycle. Mark it `actioned` once the fix exists (for a
repeat violation that means a guard, not reworded text), `declined` if it was wrong,
`superseded` if a later observation covers it, or `parked` for a real item that's deferred.
See `log.md`.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `id` | string | — | `"0003"` or `"3"`. |
| `status` | string | — | `open`, `actioned`, `declined`, `superseded`, `parked`. The words people reach for work too: `fixed`, `done`, `resolved` and `closed` mean `actioned`; `wontfix` and `rejected` mean `declined`; `duplicate` means `superseded`. |
| `resolution` | string | `""` | Appended as a dated `## Resolution` section, so history is kept rather than overwritten. |

## Returns
`{id, status, previous_status}`.

## Errors
`ValueError` for an unknown status or an id that doesn't exist.

## Implementation
`mcp-server/tools/observe/update.py`, via `_store.update`.
