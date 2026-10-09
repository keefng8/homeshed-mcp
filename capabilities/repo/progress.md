# repo.progress and repo.progress_update

Where an agent preparing a project for release says how far it has got, so the owner sees it as a percentage on a
dashboard. HomeShed's own release was the first project on it.

## How it works
- The agent calls `repo.progress_update` after each verification run and at the end of its session. It reports
  pass/fail **gates** (each with its evidence, e.g. "917 passed" or "scan of 9afaeaf: 0"), measured **metrics**
  (e.g. `readiness: 27 of 29` from `repo.readiness`), and what's **waiting on the owner**.
- The panel shows two numbers per project: release progress (gates done out of all) and readiness (the metric).
- Each waiting item keeps the date it was first reported, so the panel shows how long it has waited. The last 20
  updates are kept.
- The agent's name is the calling client's when it uses a client token; the owner's own sessions may give a display
  name with `agent`.

## repo.progress_update parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `project` | string | required | A short name: lower case letters, digits and dashes (e.g. `homeshed`). |
| `title` | string | `""` | What the project is, as the panel shows it. |
| `steps` | array | none | `[{id, title, status, evidence?}]`; status is `done`, `doing`, `waiting`, `blocked` or `todo`. Replaces the previous list. At most 40. |
| `metrics` | object | none | `{name: short text}`, at most 20. |
| `waiting_on_owner` | array | none | What only the owner can act on, at most 10: short lines, or `{text, why?, how?, link?}` (`link`: an `https://` address). |
| `note` | string | `""` | What this update checked, e.g. "checked 9afaeaf". |
| `agent` | string | `""` | A display name for the owner's own sessions; ignored for client tokens. |
| `repo` | string | `""` | The GitHub repository as `owner/name`; the panel links to it. |
| `visibility` | string | `""` | A short phrase, e.g. `private`, `public` or `not public yet`. |
| `branch` | string | `""` | The branch releases come from, e.g. `main`. |
| `version` | string | `""` | The version being released, e.g. `v0.1.0`. |
| `licence` | string | `""` | Its SPDX id, e.g. `Apache-2.0`. |
| `workspace` | string | `""` | The project's name in the tool server's project list (`GET /projects`), never a path. The owner's panel shows that project's folder from it; the folder never enters progress data. |
| `next_step` | string | `""` | One line: what happens next. |

`repo` to `next_step` left empty keep what was stored. Returns `{project, percent, done, total, updated}`.

## repo.progress parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `project` | string | `""` | Empty: a summary of every project. A name: that project with its steps and history. |

Returns `{projects: [{project, title, percent, done, total, metrics, waiting_on_owner: [{text, since, why?, how?,
link?}], agent, updated, note, repo?, repo_url?, visibility?, branch?, version?, licence?, workspace?, next_step?}]}`,
or one project's summary plus `steps` and `history` (newest first).

## Errors
Both raise `ProgressError`: a bad project name, an unknown project (read), too many steps, metrics or waiting items,
an unknown status, text over its limit, or a secret or local path in any text (every text field is checked, since
the panel shows it).

## Configuration
Stored in `DATA_DIR/usage/repo_progress.json`.

## Implementation
`tools/repo/progress.py`.

## Machine-readable definition
`progress.json`, `progress-update.json`

## Related
`repo.readiness`.
