# APIs — List / Find

## ID
`apis.list`, `apis.find`

## Purpose
A shared **API catalogue**: one place that says which APIs this server knows about. For each it records
what the API is, what it costs, whether it's self-hosted, whether it works right now, and which tool
wraps it. Every client connected to the server reads the same list, so check it before building
something: an API for the job may already be set up, or already tried and rejected.

## Parameters
`apis.list`: `category` ("llm", "search", "notify", "image", "classification", "voice", …),
`cost` (substring: "free", "free-tier", "paid"), `local` (true = self-hosted, false = external),
`status` ("live", "needs-setup", "candidate", "reference"). All optional; an unknown value just
returns no matches.

`apis.find`: `query` (plain words, e.g. "text to speech", "free web search"), `k` (1-25).
Ranking is keyword overlap across name/category/provider/how_to_use/notes, with a name match
weighted highest (an exact name match always ranks first).

## Returns
Entries of `{name, category, provider, cost, local, status, auth, how_to_use, notes}`
(`apis.find` adds `score`).

## Secrets
**Never stored.** `auth` names the *environment variable* that holds a key (e.g.
`bearer via env LOCAL_AI_GATEWAY_KEY`), never the key itself. As a second line of defence, the
loader refuses to serve the catalogue at all if anything in it looks like a real key (`sk-…`,
`tk_…`, `nvapi-…`, `ghp_…`, AWS keys).

## Adding an API
Add one entry to `api_catalog.json` and restart the server. Note the real cost, and whether prompts or
data leave your network.

## Implementation
`tools/apis/catalog.py`, catalogue `api_catalog.json`. Tests: `tests/test_apis_catalog.py`, including a
test that the shipped catalogue parses and is secret-free.
