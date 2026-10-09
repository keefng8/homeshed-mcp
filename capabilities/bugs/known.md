# Known bugs: Report / Find / List

## ID
`bugs.report`, `bugs.find`, `bugs.list`

## Purpose
Every mistake is found once, then recorded with its cause and fix, so no AI session and no person has to
rediscover it.

**Before debugging an error, call `bugs.find` with the error text.** After fixing a new one, call `bugs.report`.

## Parameters
`bugs.report`:
- `title` (3-120 characters). Also `symptom`, `error_text`, `root_cause`, `workaround`, `fix`, `component`, `guard`
  (the guard id that now prevents it), `tags`, `source`.
- `fix_status`: `open`, `workaround-only` or `fixed`. Empty keeps the current status (`open` for a new bug).
- A report with the same error fingerprint as a known bug adds an occurrence to it instead of creating a new one.

`bugs.find`: `query` (an error message or a few words), `k` (1-20).

`bugs.list`: `fix_status`, `component`, `limit` (1-100).

## Returns
Bugs as `{id: "KB-0001", title, symptom, error_text, root_cause, workaround, fix, fix_status, component, guard,
tags, fingerprint, occurrences (a count), created, updated}`. `bugs.find` adds `score`: 100 for the same error, 10
per shared word.

## How matching works
The schema follows ITIL's known-error database: "known cause, workaround exists, no fix" is a state of its own.
Matching follows Sentry-style grouping: the error text is normalised (numbers, paths, hex ids and quoted values
replaced) and hashed, so the same bug with different details matches.

## Searching GitHub when nothing matches (opt-in)
Off by default. Switch on the `bugs_search_public` setting to use it.
- When no known bug matches (best score under 30), `bugs.find` searches GitHub issues and adds up to 3 links
  (title, url, repo, state) as `outside`, cached 10 minutes.
- Only a cleaned signature of the error is sent (`signature()`: no paths, addresses, numbers, hex ids, quoted
  values or secrets).
- An optional `GITHUB_SEARCH_TOKEN` credential raises GitHub's rate limit.
- Any failure gives no links, never an error.
- Other trackers are never scraped or copied.

## Safety
Every text field is redacted before it's stored: token prefixes, `NAME=value` secrets, private keys and long
key-like runs become `[hidden]`. An unreadable store is refused, never overwritten.

## Implementation
`tools/bugs/known.py`. Store: `KNOWN_BUGS_FILE` (default `DATA_DIR/observations/known_bugs.json`). Tests:
`tests/test_known_bugs.py`.
