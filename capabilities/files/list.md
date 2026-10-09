# Files — List

## ID
`files.list`

## Purpose
List a directory's entries — names, type, size, modified time. Metadata only, matching
`system.info`'s risk tier: useful for orientation without exposing anything sensitive.

## Deliberately not built
`files.read` (file *content*) and `files.write` are real security decisions, not built here —
this container's own `/app` includes `.env` with live secrets (`MCP_AUTH_TOKEN`,
`MEMORY_CORE_BEARER`, etc.). A content-reading capability needs an explicit scoping decision
(allowlist directories? deny dotfiles? sandbox?) before it exists — see
`memory.recall_facts(category="known-gaps")` if that decision gets made later. `files.list`
itself is safe because listing reveals filenames/sizes/dates, never contents — knowing `.env`
exists and is 2KB tells an attacker nothing `ls -la` wouldn't.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `path` | string | `"."` | Directory to list. Relative paths resolve against the server's working directory (`/app` in the deployed container). |
| `limit` | integer | `100` | At most this many entries, 1 to 1000, directories first. A folder of 300 files used to come back as about 50,000 characters. |
| `detail` | string | `"compact"` | `"compact"`: each entry a string like `ls -F` (`"docs/"`, `"notes.txt (1.2 KB)"`, `"link@"`), about 2,200 characters for 100 files. `"full"`: each entry a dict with every field below, about 16,700 for the same folder. |

## Returns
`{path: str (resolved absolute path), total: int, entries: [...], more?: int}` — `total` counts every
entry; `more` appears only when `limit` left some out. Sorted directories-first, then alphabetically.
With `detail="full"` each entry is `{name, type, size_bytes, modified_iso, permissions: {octal, human}}`:
`type` is `"file"`, `"dir"`, or `"other"` (symlink/socket/etc.), `permissions.octal` the 3-digit mode
(e.g. `"644"`), `.human` `ls`-style (e.g. `"-rw-r--r--"`). Covers the FILESYSTEM index's "File
Metadata" stub (permissions, size, dates) in full.

## Errors
Raises `FilesListError` if the path doesn't exist, isn't a directory, isn't readable, or sits outside the folders
this server may read.

## Configuration
`READ_ALLOWED_ROOTS`: the folders it may list, separated by `;` on Windows and `:` elsewhere (symlinks are resolved
first, so a link can't lead out). Without it: `CLAUDE_PROJECT_DIR` (the project a stdio install serves), else the
server's working folder. `path` defaults to the first of them. The code scanners follow the same rule
(`readroots.py`). In Docker, list the mounted folders, e.g. `READ_ALLOWED_ROOTS=/app:/data`.

## Implementation
`mcp-server/tools/files/list.py`.

## Machine-readable definition
`list.json`, same directory.

## Related
None yet.
