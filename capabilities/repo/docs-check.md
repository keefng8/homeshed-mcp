# repo.docs_check

Tells you where a project's docs say something its code doesn't back. Ported from the check HomeShed's own public CI
runs (the Github prepper's `scripts/check_docs.py`), which caught six false doc claims before the first release.

## When to use it

Before publishing docs, after renaming a setting or a command, or in a review of someone else's README.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `folder` | string | `"."` | The project's root folder. It must be inside the folders this server may read (see Configuration). |
| `package` | string | `""` | The command name the docs use, e.g. `homeshed-mcp`. Default: `[project] name` in `pyproject.toml`. |
| `cli` | string | `"cli.py"` | The file with the argparse setup, relative to `folder` (Python projects). |
| `detail` | string | `"compact"` | `"compact"`: totals and at most 20 problems. `"full"`: every row. |

## What it checks
1. **Anchors (`broken-anchor`):** every `file.md#anchor` and `#anchor` link in `README.md` and `docs/` points at a real
   heading (GitHub's slug rules, headings inside code blocks don't count) or an explicit `<a id="...">`.
2. **Settings (`unread-setting`):** every variable in the first column of `docs/configuration.md`'s tables appears as a
   quoted name in the code (`.py` and `.json` outside `tests/` and `scripts/`).
3. **Commands (`unknown-command`, `unknown-option`):** every `PACKAGE subcommand --flag` the docs show (after `uvx`, in
   backticks, or starting a line) exists in the CLI file's argparse setup.

Checks 2 and 3 need the code: without the CLI file they're skipped, with a `code-checks-skipped` note, not failed.

## Returns
`{folder, docs, checks: [{id, ok, file, detail}], failed, counts, passed, more?}`. `docs` is the number of Markdown
files read; each problem's `detail` names the anchor, setting, command or option.

## Errors
Raises `DocsCheckError` if the folder doesn't exist, isn't a folder, or is outside the allowed folders; if `cli`
points outside the folder; or if `detail` isn't `"compact"` or `"full"`.

## Configuration
`READ_ALLOWED_ROOTS` (else `CLAUDE_PROJECT_DIR`, else the server's working folder) decides which folders it may read.
Walks skip virtualenvs, `node_modules` and `.git`, and stop after 3,000 files.

## Implementation
`tools/repo/docs_check.py`, ported from the Github prepper's `scripts/check_docs.py` together with its tests.

## Machine-readable definition
`docs-check.json`

## Related
`repo.readiness`.
