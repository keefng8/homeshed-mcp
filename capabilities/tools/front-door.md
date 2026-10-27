# Tools — The front door

## IDs
`tools.find`, `tools.groups`, `tools.describe`, `tools.call`, `tools.result`

## Purpose
An app that loads every tool definition (160+) pays for all of them on every turn, and a small model choosing among
that many picks the wrong one more often. The front door gives it five tools instead: find the right one, see its
inputs, run it, and keep big replies short.

## How an app gets it
On the Control Panel's API page each app has a **Tool view**: the full list, or the front door. Apps added after
27 Oct 2026 start on the front door; older ones keep the full list until the owner switches them. With the front door
the app's tool list shows only these five; every other tool it's granted still runs if called by name. The owner's own
sessions (Claude Code) keep the full list: their tool search already does this job.

## The five tools
| Tool | What it does |
|---|---|
| `tools.find(task, limit=8)` | Best-matching tools for a job, in plain words: `{"tools": [{"id", "does", "risk"}], "next"}`. Ranked by the tool's id, name, aliases and description; no model call, so it's instant and free. |
| `tools.groups(group="")` | The groups with counts and examples, or one group's tools. |
| `tools.describe(name)` | One tool's description and `inputSchema` exactly as the full list shows it (`outputSchema` when it has one). |
| `tools.call(name, arguments, fields?, to_file?)` | Runs the tool through the server's one dispatcher: the app's grant and calls-per-minute limit, input checks, switches, approvals and spend limits all apply as for a direct call. Usage counts the tool under its own name, and `tools.call` separately. |
| `tools.result(result_id, fields?, query?, offset=0, limit=50)` | Reads back a reply stored with `to_file`, a page at a time. |

Only tools the app may call are shown. With HomeShed Pro the owner can narrow that further per app (a tool set).

## Trimming a reply (`fields`)
Paths with dots, and `[]` after a list: `["items[].title", "items[].state", "count"]`. The reply keeps just those, adds
`_dropped` (how many keys went) and `_missing` (paths it didn't find; never an error). `id`, `request_id`, `status`,
`ok`, `error` and `reason` are always kept, and a reply that queued something for the owner's approval is never
trimmed. Errors pass through whole.

## Keeping a big reply on the server (`to_file`)
The reply becomes `{"result_id", "tool", "bytes", "shape" (keys and list sizes), "preview" (first 1,500 characters),
"expires_in_h": 24, "read_with"}`. `tools.result` pages through its main list (`query` filters by text, `fields` name
each item's fields). Each app keeps its 20 newest (an older one is removed and the reply says so), up to 5 MB each,
for 24 hours; only the app that made it can read it. Key-shaped secrets (tokens, private keys, KEY=... lines) are
replaced with `[hidden]` before it's stored.

## Pictures (`images` per app)
Also on the API page: **Images** inline (base64 inside the JSON, as before) or as content (MCP image blocks, which a
model reads as a picture at a fraction of the cost). Apps added after 27 Oct start on content. With content, a reply's
`image_b64` (and up to 4 in a list, e.g. a carousel) moves to image blocks and the JSON says `"image": "sent as an
image block"`. Tools that declare an output schema are left as they are on direct calls.

## HomeShed Pro
- **Tool sets**: on the API page the owner picks which tools the front door shows an app (`POST /clients/{name}/toolset`,
  `{"patterns": [...]}` or `null`). It only narrows what `tools.find` / `tools.groups` show; what may run is still
  the grant. The free HomeShed keeps the set, so nothing migrates when Pro starts or lapses: without Pro the front door
  shows the whole grant and the set waits, unused.
- **Learned ranking**: `tools.find` ranks the tools an app actually uses a little higher (at most +3, a tie-break).
- **`tools.savings`**: per app, tool definitions the front door didn't load and reply characters trimmed, with a
  rough token count (4 characters a token). The free HomeShed collects the numbers (`usage.json` "front_door").
