# Git — Diff

## ID
`git.diff`

## Purpose
Unified diff for a repository's working-tree or staged changes. Same deterministic-tool
rationale as `git.status` — exact output from `git diff`, not an LLM description of it.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `path` | string | — | Required. Directory inside (or root of) a git repository. |
| `staged` | bool | `false` | Diff staged/index changes (`git diff --cached`) instead of the working tree. |
| `file` | string | `null` | Restrict to one file, relative to the repo root. Omit for the whole repo. |
| `context_lines` | integer | `3` | Lines of context around each change, 0-20 (`git diff -U<n>`). |

## Returns
`{diff: str, truncated: bool}`. `diff` is `""` when there are no changes. `truncated` is `true`
if the real diff exceeded 50,000 chars and was cut off — ask for a narrower `file` instead of
assuming the full picture from a truncated result.

## Errors
Same failure modes as `git.status`: not a directory, not a git repository (including the
parent-directory-search caveat documented there), git not installed, command timeout (15s).

## Implementation
`mcp-server/tools/git/diff.py` — `subprocess`, argument list, same pattern as `git.status`.

## Machine-readable definition
`diff.json`, same directory.

## Related
- `git.status`
