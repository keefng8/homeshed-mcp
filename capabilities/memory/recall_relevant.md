# Memory — Recall Relevant

## ID
`memory.recall_relevant`

## Purpose
Recall messages from a session ranked by relevance to a query, not just chronological order.
`memory.recall` on its own returns exactly what was written, in the order it was written — this
reranks that same data by how much it actually overlaps with what you're asking about right now.

## Where this came from
The one piece of `reference-repos/cognitive-workspace` (see `CLAUDE.md`'s entry for the full
research) that's genuinely portable without a bigger architecture decision first. Its own
relevance check (`_check_working_memory`) is plain keyword overlap — no embeddings, no vector
store required, confirmed by reading its actual code, not assumed from the README. The rest of
that repo's value (hierarchical buffers, promotion between tiers) needs an in-process object that
mutates across many calls within one session — a real mismatch with this project's stateless-
per-call capability model, and a genuine open design question (*where* would that cross-call
state live?) deliberately not answered here. This capability sidesteps that entirely: it reads
`memory.recall`'s already-persisted history fresh on every call and only adds a ranking pass on
top, no new state of its own.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `session_id` | string | — | Required. Same `session_id` used with `memory.capture`/`.recall`. |
| `query` | string | — | Required. What you're trying to find — scored by shared words (BM25), not a formal search syntax. |
| `limit` | integer | `50` | How many messages to fetch from `memory.recall` before ranking, 1-100. |
| `max_results` | integer | `5` | How many ranked results to actually return, 1-50. |
| `max_chars` | integer | `300` | Cut each returned message to this many characters (ending "…", marked `"truncated": true`); 0 returns the full text (the default until 2026-09-29). Keeps the per-request memory check cheap. |
| `scope` | string | `"project"` | `"project"` reads this project's own memory; `"global"` reads the shared memory (facts every project may need): the owner always, and an app unless the owner switched its shared reads off on the web panel (API access). |
| `project_dir` | string | `""` | Your working folder, e.g. `C:\Users\you\my-app` or `~/my-app`. Memory is kept per project: a folder registered on the tool server (`GET`/`POST /projects`) uses that project's own memory (the longest match wins); any other folder gets its own memory on the built-in store, and the shared memory on memory-core; no folder uses the shared memory. Only the owner's sessions can choose: an app with its own client login always uses its own memory. An `X-Homelab-Project` header on the connection does the same. |

## Returns
`{"messages": [{"id", "role", "content", "timestamp", "relevance_score"}, ...],
"total_considered": int}`. `messages` is sorted by `relevance_score` descending and truncated to
`max_results`. `relevance_score` is an Okapi BM25 score (k1 1.2, b 0.75, IDF from the candidates
themselves), multiplied by a recency factor from 0.9 for the oldest candidate to 1.1 for the newest
(2026-09-29, R&D's R2/R10). A rare shared word counts for more than a common one, and a long fact doesn't
win just by being long. Before this, the score was the count of shared words, and 3 of the 5 regression
cases in `researchandimprovements/evals/recall_eval.py` were won only on the tie-break.

**Meaning, too** (R10's next step): the GPU service's `/knowledge/similarity` scores every candidate
against the query with bge-small, and results are ordered by reciprocal rank fusion (k=60) of the two
rankings. So a fact worded differently from the question (no shared words) can still rank, and each result
then carries `semantic` (cosine, -1 to 1) beside `relevance_score` (still the word score, 0 = no shared
word). If the GPU service doesn't answer within 4 s, results use words alone and the meaning arm rests
for 60 s, so recall never waits on a PC that's off.

## Errors
`ValueError` if `query` is empty/whitespace, or `max_results` is out of range (1-50).
`MemoryError` — same failure modes as `memory.recall` (not configured, backend unreachable, query
rejected), since this calls it directly.

## What this deliberately is not
Not semantic search. Word-overlap matching misses synonyms, paraphrasing, and any real conceptual
similarity — "deploy the service" and "release the app" would score zero overlap despite meaning
almost the same thing. Good enough for finding messages that literally share vocabulary with a
query; not a substitute for real embedding-based retrieval if that's ever genuinely needed.

## Implementation
`mcp-server/tools/memory/recall_relevant.py`. In-process call to `tools.memory.recall.recall` —
no new I/O beyond what `memory.recall` already does. Tests:
`mcp-server/tests/test_recall_relevant.py` — mocks `recall` directly (same boundary as
`test_delegate.py` mocking `route`/`ask`), not the underlying HTTP layer.

## Machine-readable definition
`recall_relevant.json`, same directory — `"risk": "read"` (pure computation over data
`memory.recall` already exposes; no new I/O, no state written).

## Related
- `memory.recall` — what this wraps and reranks.
- `memory.capture` — how the data being recalled got there in the first place.
