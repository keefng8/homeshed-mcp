# Graphify — Query

## ID
`graphify.query`

## Purpose
Wraps the [graphify](https://pypi.org/project/graphifyy/) CLI's `query` command rather than
reimplementing graph traversal. Runs a BFS/DFS traversal of an **already-generated**
`graphify-out/graph.json` for a natural-language question.

**A locator, not an answerer:** it returns `src=` paths ranked by term overlap, not a final answer.
Read the returned files for the answer.

## When to use
Before a broad text search across a project that already has a `graphify-out/` directory — faster
and more precise than a cold search across the tree.

## When NOT to use
- The project has no `graphify-out/graph.json` yet — this doesn't generate one. Build the graph with
  graphify first, then call this.
- An empty result can mean "not indexed", not necessarily "not present".

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `project_path` | string | — | Required. Directory containing `graphify-out/graph.json`. |
| `question` | string | — | Required. Natural-language question, max 1000 chars. |
| `budget` | integer | `2000` | Max output tokens, 100-20000. Raise this if the result says `[!] TRUNCATED`. |
| `dfs` | boolean | `false` | Depth-first instead of breadth-first traversal. |

## Returns
`{result: str, graph_path: str}` — `result` is graphify's raw text output (node list with
`src=`/`loc=`/`community=` per node, truncation notice if the budget was too small).

## Errors
Raises `GraphifyError` — `graphify` not on PATH, no graph found at
`<project_path>/graphify-out/graph.json`, empty/too-long question, budget out of range, timeout
(30s), or the CLI itself failed (its stderr, truncated to 300 chars).

## Requirements
The `graphify` command, from the `graphifyy` package (double y; Apache-2.0, pure Python with
cross-platform `tree-sitter` wheels). It is a dependency of this server, so installing the server
provides it on Windows, macOS and Linux.

## Implementation
`tools/graphify/query.py`. Tests: `tests/test_graphify_query.py` (mocked subprocess, no real graphify
needed).

## Machine-readable definition
`query.json`, same directory.
