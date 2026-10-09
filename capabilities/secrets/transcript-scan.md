# secrets.transcript_scan

Claude Code saves every conversation, including every command it ran and what came back, as plain text on your disk,
and keeps it. A key you pasted into a prompt, or one a command printed, is still there months later. This finds
them and tells you where to rotate each one. It never shows the secret itself.

## When to use it

Now, once; then after any session where a key was pasted, printed or read from a file. On one careful owner's PC it
found 11 live credentials the first time (KB-0015).

## Inputs

| Input | Meaning |
|---|---|
| `days` | Only transcripts changed in the last N days. `0` (the default) scans them all: a saved secret stays until the file is deleted. |
| `detail` | `compact` (the default): totals and at most 20 findings, newest first; `more` says what was left out. `full`: every finding. |

It reads only Claude Code's transcript folder: `CLAUDE_CONFIG_DIR/projects`, else `~/.claude/projects`, `*.jsonl`
files. Nothing else, and it takes no path from the caller. In Docker there are no transcripts, so it answers "no
Claude Code transcripts on this machine"; it's meant for a stdio install on your own PC.

## Returns

`{folder, transcripts, skipped, summary: {kind: count}, actions: {kind: what to do}, checks, failed, counts}`.
`actions` says once per kind what to do ("rotate it at console.anthropic.com > API keys", "revoke it at
github.com/settings/tokens", …), so the rows stay short. Each finding (one per secret, however many files it's in):

| Field | Meaning |
|---|---|
| `kind` | e.g. "Anthropic API key", "GitHub token", "password or secret" |
| `prefix` | the first 4 characters, only for kinds whose start is public anyway (`sk-a`, `ghp_`); empty for passwords and bearer tokens |
| `length`, `fingerprint` | its length, and the first 12 hex characters of its SHA-256, so you can match it against a key you hold without anyone seeing it |
| `projects`, `files`, `occurrences` | which project folders (up to 5), how many files, how many times |
| `first_seen`, `last_seen` | dates, from the transcript's own timestamps |

Rotating is the fix. Deleting the transcript alone doesn't help if the key was ever used or copied elsewhere.

## Known limitations

- Pattern matching: a key in a format it doesn't know is missed, and "password or secret" / "token in a setting" can
  flag a value that isn't secret (an example, a hash). Values that look like placeholders are skipped.
- It can't tell whether a key is still live: rotate, or check it at the provider.
- A full scan reads every transcript: about 80 seconds for 67 transcripts on the owner's PC. Use `days` for a quick
  look.
- Transcripts over 200 MB are skipped and counted in `skipped`.

## Risks

Read-only, and only that one folder. The output holds no secret: a public 4-character prefix at most, a length, and
a 12-character fingerprint, which can't be turned back into the key.
