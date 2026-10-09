# Git — Commit

## ID
`git.commit`

## Purpose
Stage and create a git commit. Off until you switch it on (`ENABLE_TOOLS`); once on, it runs without a
`dry_run` or confirmation step, so any client allowed to call it can commit straight away.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `path` | string | — | Required. Directory inside a git repository. |
| `message` | string | — | Required. Commit message. |
| `files` | array | — | Paths (relative to `path`) to stage before committing. |
| `add_all` | boolean | `false` | Stage every tracked change instead of an explicit list. |

Pass either `files` or `add_all=true`: the tool never guesses what to stage.

## Concurrency
Several calls at once against the same repo can hit git's own `.git/index.lock`. The tool retries
automatically, up to 3 attempts with a short backoff, **only** on lock-contention errors. A real merge
conflict or a bad pathspec is reported at once, never retried. Tested with 3 concurrent commits to one
repo: all 3 succeeded.

## Returns
`{"commit": str, "branch": str, "files_committed": [str]}`

## Errors
`GitError` — not a directory, not a repo, empty message, neither `files` nor `add_all` given,
nothing staged to commit, or the underlying `git add`/`git commit` failed.

## Implementation
`tools/git/commit.py`. Plain `subprocess.run` with an argv list (never `shell=True`).
Tests: `tests/test_git_commit.py`, run against a real temporary repo, not mocked.

## Machine-readable definition
`commit.json` — `"risk": "write"`.

## Related
- `git.status` — check what would be staged before committing.
- `git.branch` / `git.push` — the other git write tools.
