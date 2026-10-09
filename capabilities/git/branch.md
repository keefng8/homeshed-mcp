# Git — Branch

## ID
`git.branch`

## Purpose
Create and/or switch to a git branch. **Fully autonomous — no confirmation step**, same
2026-09-24 decision as `git.commit`.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `path` | string | — | Required. Directory inside a git repository. |
| `name` | string | — | Required. Branch name to create/switch to. |
| `create` | boolean | `true` | Create `name` if missing; if `false`, error when it's missing. |
| `base` | string | — | Ref to branch from when creating (default: current HEAD). |

## Returns
`{"branch": str, "created": bool, "previous_branch": str}`

## Errors
`GitError` — not a directory, not a repo, empty `name`, branch missing when `create=false`, or
the underlying `git checkout` failed.

## Implementation
`mcp-server/tools/git/branch.py`. Tests: `mcp-server/tests/test_git_branch.py`, real temp repo.

## Machine-readable definition
`branch.json` — `"risk": "write"`.

## Related
- `git.status`, `git.commit` — the rest of the write-operation set added the same day.
