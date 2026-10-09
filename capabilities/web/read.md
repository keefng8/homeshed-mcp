# Web — Read

## ID
`web.read`

## Purpose
Fetch a public webpage as Markdown via Jina Reader (r.jina.ai) — zero API key, zero account.
Ported from the `Agent-Reach` reference repo's `channels/web.py` (MIT license), per
`.claude/external-audit-agent-reach.md`'s ADAPT verdict.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `url` | string | — | Required. URL of the webpage to fetch. Scheme optional, defaults to `https`. |

## Returns
`{url: str, content: str}` — `content` is the page as Markdown, exactly as Jina Reader returns
it (not this project's own rendering).

## Security
Rejects localhost, private/link-local IPs, and cloud metadata endpoints before any request is
made (SSRF defense) — ported from Agent-Reach's `utils/url.py`'s `normalize_public_http_url`.
Don't loosen this casually; it's the reason this capability is safe to expose at all.

## Not supported
JavaScript-rendered content beyond what Jina Reader itself handles, responses over 5MB, and
sites behind an anti-bot/Cloudflare challenge — the last of these raises a clear error rather
than silently returning a captcha page as if it were real content. Verified live 2026-09-22
against 3 real URLs: `wikipedia.org` and `news.ycombinator.com` succeeded;
`github.com/anthropics` returned a real HTTP 403 from Jina Reader (GitHub's own scraping policy,
not a bug here — the error surfaces honestly rather than hiding it). Some sites will 403 through
Jina Reader; that's expected third-party behavior, not something to work around.

## Errors
Raises `WebReadError` for a non-public URL, an unreachable page, an oversized response, or an
anti-bot challenge page.

## Implementation
`mcp-server/tools/web/read.py`.

## Machine-readable definition
`read.json`, same directory.

## Related
None yet.
