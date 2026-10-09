# Git — Push

## ID
`git.push`

## Purpose
Push the current branch to a remote. Off until you switch it on (`ENABLE_TOOLS`); once on, it runs
without a confirmation step, like `git.commit`. It is the most consequential git write tool, because it
publishes changes somewhere else. `force` defaults to `false`; when `true` it uses `--force-with-lease`
rather than a bare `--force`, so a force-push still refuses if the remote has commits this push doesn't
know about. That guards against overwriting someone else's work by accident.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `path` | string | — | Required. Directory inside a git repository. |
| `remote` | string | `"origin"` | Remote to push to. |
| `branch` | string | — | Branch to push (default: current branch). |
| `force` | boolean | `false` | Use `--force-with-lease` instead of a plain push. |

## Returns
`{"branch": str, "remote": str, "forced": bool}`

## Errors
`GitError` — not a directory, not a repo, or the push failed (rejected, auth failure, network
error, lease mismatch — real `git` stderr included, truncated to 300 chars).

## Implementation
`tools/git/push.py`. 120s timeout. Tests: `tests/test_git_push.py`, a real local repo pair (no
network needed).

## Machine-readable definition
`push.json` — `"risk": "write"`.

## Related
- `git.commit`, `git.pull` — the other git write tools.
