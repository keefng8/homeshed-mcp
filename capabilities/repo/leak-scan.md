# repo.leak_scan

Finds private details and secrets in a folder that's about to go public, and tells you where, never what. From the
release tools behind HomeShed's own public copy, which found 432 private details in 82 files of a project that looked
clean, and brought back more after five later commits.

## When to use it

Before making a repository public, and on every commit after that: private details come back with new work.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `folder` | string | `"."` | The folder to scan. It must be inside the folders this server may read (see Configuration). |
| `patterns` | object | none | `{name: regex}` for your private details (names, domains, private addresses, folder paths). Default: the folder's `.repo-release.json`. At most 50 patterns, 300 characters each. |
| `secrets` | boolean | `true` | Also look for secrets: API keys, tokens, private keys, passwords in settings. |
| `detail` | string | `"compact"` | `"compact"`: totals and at most 20 findings. `"full"`: every finding. |

## The folder's `.repo-release.json`
Keeps your private patterns next to the project instead of in every call. The file itself is never scanned.
```json
{
  "leak_patterns": {"owner name": "\\bJane Doe\\b", "home network": "\\b192\\.168\\.\\d+\\.\\d+\\b"},
  "leak_exempt": {"home network": ["tests/"]},
  "allowed_examples": ["192.168.0.0/16"],
  "public_identity": ["janedoe"]
}
```
`leak_exempt` skips a pattern under the given path prefixes (e.g. tests that check blocking); `allowed_examples` and
`public_identity` are literal text that's fine to publish, removed from a line before it's checked.

## Returns
`{folder, source, files, checks: [{id, kind, ok, file, line, prefix?}], failed, counts, passed, more?}`. `id` is the
pattern's name or the secret's kind; `kind` is `"leak"` or `"secret"`. The matched text is never returned: a secret
shows at most its first 4 characters, and only for kinds whose start is public anyway (`ghp_`, `sk-ant-`). A line
marked `secret-scan: allow` or `leak-scan: allow` is skipped. `source` says where the patterns came from: `"call"`,
`".repo-release.json"` or `"none"`.

## Errors
Raises `LeakScanError` if the folder doesn't exist, isn't a folder, or is outside the allowed folders; if a pattern is
invalid, longer than 300 characters, or nests quantifiers like `(a+)+` (Python's regular expressions have no time
limit, so that shape could run for ever); if there are more than 50 patterns; if `.repo-release.json` isn't valid
JSON; or if `detail` isn't `"compact"` or `"full"`.

## Configuration
`READ_ALLOWED_ROOTS` (else `CLAUDE_PROJECT_DIR`, else the server's working folder) decides which folders it may read.
It skips `.git`, `node_modules`, virtualenvs, binary files, files over 1 MB and lines over 2,000 characters, and stops
after 5,000 files.

## Implementation
`tools/repo/leak_scan.py`; the secret kinds are `secrets.transcript_scan`'s.

## Machine-readable definition
`leak-scan.json`

## Related
`repo.readiness`, `secrets.transcript_scan`.
