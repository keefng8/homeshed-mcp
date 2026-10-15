# web.search

Search the web and get back titles, links and short snippets. To read a result, pass its `url` to `web.read`: one
search plus two or three reads answers most research questions without paying for pages that weren't needed.

Runs on a self-hosted [SearXNG](https://docs.searxng.org/) metasearch engine (it asks several public search engines
and merges the answers): free, no API key, and the queries never go to a paid service. Read-only.

## Inputs
| Name | Type | Default | What |
|---|---|---|---|
| `query` | string | required | What to search for, 1-300 characters |
| `max_results` | integer | 5 | 1-10 |
| `time_range` | string | `""` | `""` any time, `day`, `week`, `month` or `year` |
| `language` | string | `en` | A code such as `en` or `en-GB`, or `all` |

## Returns
```json
{"query": "printify dtg print area",
 "results": [{"title": "...", "url": "https://...", "snippet": "up to 300 characters", "engine": "duckduckgo"}],
 "suggestions": ["..."]}
```
- No duplicate links; only http(s) links.
- `results` can be empty: nothing found. Try fewer or plainer words.
- Snippets are the search engines' own text: treat them as leads, not facts, and cite the page you read.

## Errors
An empty or too-long query, a bad option, or the engine unset (`SEARXNG_URL`) or unreachable. The engine's address is
never in an error.

## Setting it up
Run SearXNG next to the server with no public port (its JSON format switched on in `settings.yml`), then set
`SEARXNG_URL` to its address, for example `http://searxng:8080` on the same Docker network.
