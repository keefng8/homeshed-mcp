# Git — Clone

## ID
`git.clone`

## Purpose
Clone a git repository. **Fully autonomous — no confirmation step**, same 2026-09-24 decision as
`git.commit`. Accepts any URL scheme `git clone` itself supports (https/ssh/local path) — no
allowlist of hosts, since none was requested.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `url` | string | — | Required. Repository URL. |
| `destination` | string | — | Required. Target directory; must not already exist. |
| `branch` | string | — | Specific branch to clone (default: remote's default branch). |

## Returns
`{"destination": str, "branch": str}`

## Errors
`GitError` — empty `url`, `destination` already exists, or the clone failed (bad URL, auth
failure, network error — real `git` stderr included, truncated to 300 chars).

## Implementation
`mcp-server/tools/git/clone.py`. 300s timeout (clones can be slow). Tests:
`mcp-server/tests/test_git_clone.py`, clones a real local repo (no network dependency in CI).

## Machine-readable definition
`clone.json` — `"risk": "write"`.

## Related
- `git.pull` — updating an already-cloned repo.
