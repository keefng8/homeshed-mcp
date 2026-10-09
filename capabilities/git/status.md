# Git — Status

## ID
`git.status`

## Purpose
Repository state at a glance: branch, ahead/behind tracking, staged/unstaged/untracked files.
Deterministic (per `.claude/local-ai-operating-rules.md` §2) — never guess repo state with an
LLM when `git status` gives it exactly.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `path` | string | — | Required. Directory inside (or root of) a git repository. |

## Returns
`{branch, upstream, ahead, behind, staged: [str], unstaged: [str], untracked: [str]}`

`staged`/`unstaged` entries for a rename are the raw `old -> new` git output, not split apart —
good enough to see what happened, not parsed further. `upstream` is `null` if the branch has no
tracking remote.

## Errors
Raises with a clear message for: path doesn't exist/isn't a directory, path isn't a git
repository, git isn't installed, or the command times out (15s).

## Real caveat, found while testing this
Git searches *parent* directories for a `.git` — "not a git repository" only happens if none
exist anywhere up the tree. Confirmed live: `C:\Users\<user>` on the build machine is itself a
git repo, so calling this on almost any subdirectory under it (temp dirs, `Desktop`, `Documents`,
anything) returns that unrelated repo's status instead of erroring — including its full
untracked-file list, which on a home directory can mean hundreds of entries (`.ssh/`, `.gnupg/`,
`NTUSER.DAT`, ...). This is git's real, standard behavior, not a bug in this wrapper — but a
caller should not assume "no error" means "the path itself is a repo root." Check the returned
`branch`/file lists look plausible for the path asked about.

## Implementation
`mcp-server/tools/git/status.py` — shells out to `git status --porcelain=v1 -b` via `subprocess`
(argument list, never `shell=True`) rather than adding a GitPython dependency. Git is already
required tooling, not something to wrap in a library for one read-only command.

## Machine-readable definition
`status.json`, same directory.

## Related
- `git.diff`
