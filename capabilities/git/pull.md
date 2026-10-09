# Git — Pull

## ID
`git.pull`

## Purpose
Pull changes into the current branch. **Fully autonomous — no confirmation step**, same
2026-09-24 decision as `git.commit`.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `path` | string | — | Required. Directory inside a git repository. |
| `remote` | string | `"origin"` | Remote to pull from. |
| `branch` | string | — | Remote branch to pull (default: current branch's upstream). |

## Returns
`{"branch": str, "summary": str}` — `summary` is git's own one-line result ("Fast-forward",
"Already up to date.", etc).

## Errors
`GitError` — not a directory, not a repo, or the pull failed (conflict, no upstream, network
error — real `git` stderr included, truncated to 300 chars). A merge conflict is **not**
auto-resolved — it surfaces as an error, the repo is left in git's own conflicted state exactly
as a manual `git pull` would leave it.

## Implementation
`mcp-server/tools/git/pull.py`. 120s timeout. Tests: `mcp-server/tests/test_git_pull.py`, real
local repo pair (no network dependency in CI).

## Machine-readable definition
`pull.json` — `"risk": "write"`.

## Related
- `git.push` — the other direction.
