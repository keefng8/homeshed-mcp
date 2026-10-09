# Rule packs

A rule pack is a list of checks that run before Claude Code runs a command or writes a file. When a check matches,
Claude is stopped and told what to do instead, or warned while the action goes ahead. The checks and your own packs
are free. [HomeShed Pro](../README.md#homeshed-pro) adds curated packs, kept up to date.

## Turn the checks on

The checks run through HomeShed's hook in Claude Code, at four moments: before each tool call, when you send a
prompt, when a session starts, and when a turn ends. Add it:

```bash
uvx homeshed-mcp packs on
```

It shows the exact entries it will add to Claude Code's settings, keeps a copy of the settings file first
(`settings.json.homeshed-backup`), and asks `Add it? [y/N]`. Without a terminal it changes nothing; add `--yes` to
skip the question in a script.

The hook runs a copy of HomeShed's check engine with your own Python. It uses only Python's standard library, sends
nothing anywhere, and if anything goes wrong it lets the action through: a broken check never stops your work. Some
built-in checks read the end of the session's transcript, which Claude Code keeps on your machine, and keep a small
note per session in HomeShed's data folder. After upgrading HomeShed, run `uvx homeshed-mcp packs on` again;
`uvx homeshed-mcp doctor` tells you when it's needed.

See what's installed and whether the hook is on:

```bash
uvx homeshed-mcp packs list
```

## Write your own pack

A pack is one JSON file. This one, `careful-git.json`, stops `git push --force` but lets
`git push --force-with-lease` through:

```json
{
  "formatVersion": 1, "slug": "careful-git", "title": "Careful git", "version": "1.0.0", "tier": "free",
  "summary": "Stops force-pushes that rewrite shared history.",
  "rules": [{"id": "no-force-push", "title": "No force-push", "do": "Use --force-with-lease, which refuses if the remote moved.",
             "why": "A plain --force can delete other people's commits.", "scope": "git", "enforced_by": ["git.force-push"]}],
  "guards": [{"id": "git.force-push", "rule": "no-force-push", "kind": "command", "tools": ["Bash", "PowerShell"],
              "match_any": ["git\\s+push\\b.*--force(?!-with-lease)"],
              "message": "A plain --force push can delete other people's commits. Use --force-with-lease instead.",
              "examples": {"block": ["git push --force origin main"],
                           "allow": ["git push origin main", "git push --force-with-lease origin main"]}}]
}
```

Check it, then install it:

```bash
uvx homeshed-mcp packs check careful-git.json
```

```bash
uvx homeshed-mcp packs add careful-git.json
```

`packs check` prints `Good: this pack would install.` or the reason it wouldn't. Claude follows an installed pack
from its next action, once the hook is on.

## The format

**The pack**

| Field | What it holds |
|---|---|
| `formatVersion` | `1` |
| `slug` | Its name: lowercase letters, digits and dashes, 2 to 41 characters, starting with a letter |
| `title`, `version`, `summary` | Shown by `packs list` |
| `tier` | `"free"` or `"pro"` |
| `rules` | What the pack asks of Claude, in words |
| `guards` | The checks that enforce those rules |

**A rule**: `id`, `title`, `do` (what to do instead), `why`, `scope` (a word for what it covers, such as `git`) and
`enforced_by` (the ids of the guards that check it).

**A guard**

| Field | What it holds |
|---|---|
| `id` | Letters, digits, dots, dashes and underscores; 64 at most |
| `rule` | The id of the rule it enforces |
| `kind` | `"command"` (patterns, below) or `"builtin"` (names one of HomeShed's [built-in checks](#built-in-checks)). A pack never runs a script |
| `tools` | Any of `Bash`, `PowerShell`, `Write`, `Edit`, `NotebookEdit`, or a tool-server tool such as `mcp__homeshed-mcp__git_push` |
| `field` | What to read: `command` (the default), `file_path`, `content`, or `input` (a tool-server call's arguments as JSON) |
| `match_any` | Patterns (regular expressions); the guard fires when one matches |
| `match_all`, `unless`, `paths`, `skip_paths` | Optional lists of patterns: all must match / none may match / only these file paths / not these file paths |
| `ignore_case`, `ignore_heredocs` | Optional, `true` or `false` |
| `decision` | `block` (the default), `ask` (Claude Code asks you to confirm), `warn` (Claude is told and the action runs) or `remind` (a quiet note) |
| `message` | What Claude reads: plain text, 300 characters at most. HomeShed adds which pack it came from |
| `known_bug` | Optional: a known-bug id such as `KB-0031`, so Claude can look up the fix with `bugs.find` |
| `when` | Optional: `{"os": ["windows", "linux", "macos"]}` and/or `{"installed": "rtk"}` (a program on PATH) |
| `examples` | `{"block": [...], "allow": [...]}`, both required: text for the field, or an object of the tool's arguments |

## Built-in checks

Some rules can't be checked with a pattern: they count, remember, or read the conversation so far. These checks ship
inside HomeShed, and a pack names one, with optional settings inside fixed limits:

```json
{"id": "polling.guard", "rule": "never-poll", "kind": "builtin", "check": "polling", "decision": "block"}
```

| `check` | What it catches |
|---|---|
| `polling` | Waiting by sleeping, or asking the same thing over and over, instead of waiting to be told. A short pause, one bounded wait inside a command, and background commands are fine |
| `context-alarm` | The conversation filling up: a note to save decisions to memory before Claude Code compacts it |
| `delegation` | Routine work Claude did itself when your own model (HomeShed's `local_ai` tools) could have done it |
| `peer-message-length` | Long messages to another Claude session: 1,500 characters by default, three times that for a message marked `[long]` |
| `peer-request` | A message from another session: a note to check it like your own request |
| `peer-handoff` | Work handed to another Claude before the request was checked with memory and routing |
| `owed-reply` | Another session asked something and is waiting: a reply first, before the turn ends (held once) |
| `session-note` | The pack's own note, added when a session starts, resumes or is compacted |

## Where a pack came from

`packs list` shows each pack's source: **HomeShed Pro**, **added from a file**, or **unverified**. A pack whose file
was changed after it was installed, or that was copied into the folder by hand, is unverified: its checks still run,
but it can't ask you anything (an `ask` becomes a warning) or add a session note. `packs add` shows a pack's session
note and asks before installing it, so you see everything Claude will be told at the start of each session.

## What makes a pack valid

- Every `block` example is blocked by its guard, and every `allow` example gets through the whole pack.
- Each pattern compiles and is 400 characters at most. A repeated group with a repeat inside, such as `(a+)+`, is
  refused unless each round starts with one fixed character (as in `(?:-\w+\s+)*`). Every pattern is also timed
  against long input before the pack is saved, and one that runs away is refused.
- The file is under 256 KB of strict JSON: no `NaN` or `Infinity`, and no key twice.
- Messages are plain text: no line breaks, control characters or terminal colour codes.

## HomeShed Pro packs

Connect once with a key made on the Pro website (your account page, **Access keys**; it starts with `mav_`):

```bash
uvx homeshed-mcp pro connect
```

Then install a pack by its name: `token-saver`, `safe-operator`, `team-sessions`, `release-ready` or `beginner-mode`.
For example:

```bash
uvx homeshed-mcp packs install release-ready
```

`uvx homeshed-mcp pro status` shows the membership; `uvx homeshed-mcp pro disconnect` forgets the key here. Packs you've
installed keep working if Pro ends.

## With HomeShed in Docker

Run the commands on the machine where Claude Code runs, in the folder with your `docker-compose.yml` and `.env`, and
add the server's address so they go through it:

```bash
uvx homeshed-mcp pro connect --server http://127.0.0.1:8765
```

The owner token comes from that `.env` (or `--env-file`), and `HOMESHED_URL` can stand in for `--server`. `packs on`,
`packs add` and `packs check` work locally and don't need it.

## Turn it off or remove a pack

```bash
uvx homeshed-mcp packs remove careful-git
```

```bash
uvx homeshed-mcp packs off
```

`packs off` removes only HomeShed's hook from Claude Code's settings; your other hooks stay.
