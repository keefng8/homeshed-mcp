# Language models — relay (llm.chat, llm.providers.list)

## ID
`llm.chat` (risk `write`: it spends money), `llm.providers.list` (risk `read`)

## Purpose
Let an app (for example one on another machine) think with a **paid** language model without ever holding its key
(2026-10-04). The app calls `llm.chat` with its own client token; the tool server reads the key from the Control
Panel's vault, calls the provider, and returns a compact answer. The free local model stays `local_ai.*`; this relay
is for the times quality matters, and it is off until the owner switches it on.

## Providers
| Provider | Key (vault name) | Host | Default model | Other models |
|---|---|---|---|---|
| `xai` | `XAI_API_KEY` | `api.x.ai` (`POST /v1/responses`) | `grok-4.3` | `grok-4.7`, `grok-4.6`, `grok-4.5`, `grok-4.20-0309-non-reasoning`, `grok-4.20-0309-reasoning`, `grok-build-0.1` |

`anthropic` (Claude, 2026-10-22): the Messages API, key `CLAUDE_WORKSPACE_API`, default `claude-haiku-5-5` (sees
images), temperature not sent (its models refuse it). Every provider also has a monthly spend limit (Settings >
Language models, "most spend a month", default no monthly stop): requests stop once the calendar month's estimated
spend reaches it. `llm.providers.list` shows `monthly_credit_usd`, `cost_month_usd` and `credit_left_usd`.
`openai` (2026-10-23): the Responses API, key `OPENAI_API_KEY`, default `gpt-5.4-mini`, temperature not sent. `gemini` (2026-10-23): Google's OpenAI-compatible Chat Completions (`generativelanguage.googleapis.com/v1beta/openai/chat/completions`), key `GEMINI_API_KEY` (the same key the Nano Banana image provider uses), default `gemini-3.8-flash`. Google lists price rises (gemini-4-argon from 1 Dec 2026, the Flash models from 1 Jan 2027): update the table then. Both are off with a cap of 0 until the owner switches them on. Prices per 1M tokens are in `llm.providers.list`
(docs.x.ai/docs/models, 2026-10-04; grok-4.3 $1.25 in / $0.20 cached / $2.50 out). xAI reports each request's exact
cost (`cost_in_usd_ticks`), used when present (`cost_source: "xai"`); otherwise it's estimated from the table.

## Switches (owner only, live)
Control Panel > Settings > AI and tools > **Language models** (runtime settings in `usage/settings.json`; `/settings`
is owner-token only, so a client can read them through `llm.providers.list` but never change them):
| Key | Type | Default | Meaning |
|---|---|---|---|
| `llm_provider_xai_enabled` | on/off | **off** | the relay may use xAI |
| `llm_daily_cap_xai` | select `0,10,25,50,100,250,500,1000` | **`0`** | requests a day for every app together; 0 = none at all |
| `llm_daily_tokens_xai` | select `0,50000,…,2000000` | `0` | input + output + reasoning tokens a day; 0 = no token limit (the request cap still applies) |

Refusals name the reason: switched off, `XAI_API_KEY` not stored, cap 0, today's request cap or token cap reached.
An unreadable settings store means off. Requests are counted when they start (failed ones count); tokens and cost
when they finish. Days are the server's local date.

## llm.chat
Arguments: `messages` (1-40 `{role: user|assistant|system, content}`, oldest first), `system` (≤ 8,000 chars),
together ≤ 200,000 characters (system ≤ 32,000); `model` (empty: the default); `max_tokens` 1-16000 (default 1024;
reasoning tokens are billed on top, and thinking models spend them from this budget); `temperature` 0-2 (optional);
`provider` (`xai` default, `anthropic`, `openai`, `gemini`). Limits widened 2026-10-23 (the owner) for book chapter
batches; the wait grows with max_tokens (90 s, plus 1 s per 25 tokens past 2,048, at most 10 minutes).

Returns `{"text", "provider", "model", "truncated", "finish", "usage": {"input_tokens", "output_tokens",
"reasoning_tokens", "cached_tokens", "cost_usd", "cost_source"}, "used_today", "daily_cap"}`:
`text` is cut at 64,000 characters, and `truncated` is true when it was cut or the model stopped at
`max_tokens` (`finish: "incomplete"`).

## Safety
- The key is read by name with `vault.secret` and only ever put in the `Authorization` header of a request to
  `https://api.x.ai/v1/responses`. Redirects refused, 5 s connect / 90 s timeout, 2 MB answer cap.
- Errors carry the HTTP status and the provider's short reason, with URLs and the key scrubbed out.
- `store: false`: xAI keeps nothing for later retrieval.
- The usage log (`usage/llm_usage.jsonl`, rotated at 2 MB) and today's counts (`usage/llm_daily.json`) keep client,
  provider, model, tokens, cost and time; never a prompt or an answer.

## Granting it to a client
`llm.chat` is risk `write`, so the `read` preset never includes it: grant the exact id. Control Panel > API access >
the app's card > tick `llm.chat` (and `llm.providers.list`), or `POST /clients/<name>/tools/llm.chat/grant` with
the owner token.
