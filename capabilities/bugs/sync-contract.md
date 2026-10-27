# Known bugs: the shared contract (v1)

Two bug stores can exist on one machine: HomeShed's (`bugs.*`) and the HomeShed Library's. Each must work fully alone,
because a user may have only one of them. With both, they act as one: one catches failures, both hold every kept bug,
and a bug is never stored, counted or shown twice. Each side writes its own code against this contract and the shared
test examples in `mcp-server/tests/fixtures/bug_contract_v1_vectors.json`.

## Who does what
- **HomeShed**: catching failures (when present and healthy), suggestions (`bugs.seen`) and the owner's keep/dismiss,
  and the Pro known-bugs feed (a paid feature).
- **Library**: its own moments (pitfalls before an agent's first code in a language, the end-of-task "did the fix
  work, save it" ask, a Bugs page), catching failures when HomeShed isn't there or isn't working, and sharing bug
  lists with library-only users in the same pack format.
- Suggestions and raw failure counts stay in HomeShed. Only kept bugs are shared.

## Who catches a failure
Both sides compute the same rule, so it is never both and never neither:

> HomeShed catches when its failure hook (`known-bug-hint.py`) is in the Claude Code settings that apply (the user's
> `~/.claude/settings*.json`, or `.claude/settings.json` / `.claude/settings.local.json` in the event's `cwd` or any
> parent folder) **and**
> `~/.homelab/bug-capture.json` says its last call to the store worked (`{"ok": true, "at": <unix seconds>}`)
> within the last 24 hours. Otherwise the library catches.

HomeShed's hook writes that file after every call: `ok: true` when the store answered, `ok: false` when it didn't.
The library's hook reads it and stands down only when the rule says HomeShed catches. The library's own moments run
either way.

## The record
| Field | Meaning |
|---|---|
| `origin`, `origin_id` | The store that created the bug, and its id there (`homeshed`/`KB-0012`, `library`/...). Never changes. |
| `title`, `symptom`, `error_text`, `root_cause`, `workaround`, `fix`, `component`, `guard`, `tags` | As in `bugs.report`. |
| `fix_status` | `open` < `workaround-only` < `fixed`. |
| `fp`, `fp_version` | The fingerprint of `error_text` and the algorithm version (1 now). `""` never matches. |
| `occurrences` | Per store: `{"homeshed": 3, "library": 1}`. Each store bumps only its own key; the total is the sum. |
| `regressed` | Per store, like `occurrences`: times it happened again after `fixed`. A hint for a regressed bug says so. |
| `rev` | An edit counter: +1 on every local edit of a text field, status or tags. |
| `content_hash` | SHA-256 of the canonical JSON (sorted keys, no spaces) of the shareable fields: the text fields, `tags`, `fix_status`. |
| `deleted` | A tombstone: true once deleted or dismissed. Kept so a full swap never brings it back. |
| `audience` | `private` (default) or `public` (the Pro feed). Only the owner changes it. |
| `conflicts` | Text the other store had at the same `rev`, kept for the owner to choose. |

## Merging a record from the other store
1. **Match**: the same `origin` + `origin_id`; otherwise the same non-empty `fp` with the same `fp_version`.
   No match: add it.
2. **Status**: the higher of the two in the order above. A fix is never lost.
3. **Text fields and tags**: the higher `rev` wins. Equal `rev` with different text: keep yours, put theirs in
   `conflicts`, and flag the bug for the owner. No clock time is used anywhere in a merge.
4. **Counts** (`occurrences`, `regressed`): per store key, the larger of the two.
5. **Tombstones**: a `deleted` record wins when its `rev` is at least the other's.
6. **Secrets**: every text field is cleaned again on the way in, as on every write (tokens, keys, `NAME=value`
   secrets); a pack meant for other users is also cleaned of private addresses, user folders and user@host.

## Linking the two (the owner or an end user, once)
- **Health is automatic.** The catcher rule reads a file on the user's own PC; nothing to set.
- **The link is two values, entered once:**
  - The library's address in HomeShed's Settings > Known bugs. With a helper PC, that is the same field as
    Settings > Library address; the Control Panel saves it to both.
  - The library's key under Credentials as `LIBRARY_BUGS_KEY`. The library's dashboard gives this key from a
    "Connect HomeShed" button.
- With neither value set, nothing is sent and each store works alone.
- **The library never needs HomeShed's address.** HomeShed makes every call (below).
- Both dashboards show the link's state: linked, last exchange, pending changes, last error.

## Moving changes
- **Transport**: the known-bugs pack, `formatVersion` 2: `{"formatVersion": 2, "kind": "known-bugs", "from":
  "homeshed"|"library", "since": <cursor or null>, "bugs": [records]}`.
- **One call**: HomeShed sends `POST {library}/bugs/exchange` with `Authorization: Bearer <LIBRARY_BUGS_KEY>` and
  a pack of what changed on its side. The library merges it and replies `{"bugs": [records changed on its side since
  "since"], "cursor": "<opaque>"}`. The library leaves out records that came from this call. HomeShed keeps the
  cursor for the next call.
- **First link** (when Settings has the library's address and both answer): one full swap of both lists, tombstones
  included.
- **After that**: HomeShed exchanges in the background after a bug changes on its side, and on a lookup when the last
  exchange is over an hour old. This is how the library's changes arrive. A change the library hasn't acknowledged
  is sent again on the next call. Nothing is polled on a timer, and a failing tool call never waits.
- **No echo**: never push a change that arrived by sync, and skip a push when the other store already reported that
  `content_hash`. Merges are idempotent, so a bounce does nothing.
- **Hints** read only the local store, which holds the other store's bugs too.
- **Catches counter** (optional, for the library's "Caught by HomeShed" box): the pack may carry `"catches": {"since":
  unix_s, "week": n, "with_fix": n}`, meaning platform failures since the start of the UTC day 6 days ago, and how many
  of them matched a known bug that has a fix or workaround (`bugs.seen` counts these per day). The library keeps only
  the latest block and replies `"catches": "kept"` or the reason it dropped the block. HomeShed stores that reply in
  its exchange state. A failure that's counted asks for an exchange only when the last one is over an hour old.

## Fingerprint v1
The steps are in `mcp-server/tests/fixtures/bug_contract_v1_vectors.json` with test examples. Both sides must produce exactly those fingerprints.
A new algorithm gets a new `fp_version`: each store re-fingerprints its own records locally, and new examples cover
the new version.
