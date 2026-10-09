# Local AI — Ask

## ID
`local_ai.ask`

## Purpose
Send one prompt to a model you configured and get the answer back. It is a cheap first pass, not a
replacement for Claude: work a smaller model handles fine doesn't need to spend Claude tokens. Callers
choose between the models listed in `local_ai_backends.json`, never an arbitrary URL.

## Which model answers: `model="auto"` (the default)
Models come from `local_ai_backends.json` (or the file `LOCAL_AI_BACKENDS_FILE` names): one entry per
model, so adding one needs no code change. `auto` tries them and returns the first real answer.

- **Order:** lower `tier` first (your own models, which are free and private, before cloud ones). Within
  a tier the starting model rotates, so every model gets work.
- **Discovery:** an entry marked `"discover": true` asks an OpenAI-compatible server which models it
  serves (cached 5 minutes), so a model you add there is picked up automatically.
- **Busy:** before using a local llama.cpp model it reads the server's `/metrics`; a server with every
  slot in use is skipped as `busy`. Unreadable metrics don't skip it: the call itself is the test.
- **Failures:** a timeout, connection error, non-200, bad shape or empty text moves on to the next
  model, and a failing model is benched for a short while before it is tried again. The result's
  `backend` says who answered. If everything fails, the error lists every reason (never URLs or keys).
- **`allow_cloud=False`** skips cloud models, so the prompt stays on machines you run. **Prompts to a
  cloud model leave your network**: use `False` for anything sensitive. The `local_ai_allow_cloud`
  setting switched off forces this for every call.
- **An explicit model name (or alias) means exactly that model,** with no fallback.
- Every call adds one line to an in-memory decision log (which model answered, which were skipped and
  why, timing; never the prompt or the answer), readable at `/local-ai/decisions`.

## When to use
- Analysis, summarising, classification, extraction on a single piece of text.
- A first look before escalating the hard part to Claude.
- Repetitive processing (categorise files, extract structured fields).

## When NOT to use
- Complex architectural decisions, difficult debugging, multi-system reasoning — ask Claude directly.
- Anything needing high confidence: the model's output isn't verified or graded here.
- Multi-turn conversation: this is single-turn only (`prompt` in, `text` out, no history).
- Exact answers: prefer a deterministic tool (`reasoning.solve`, `graphify.query`, `git.status`,
  `docker.container.inspect`) when one exists.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `prompt` | string | — | Required. Single instruction/question, max 32,000 chars. |
| `model` | string | `"auto"` | `"auto"` (see above), or one model's name or alias from `local_ai_backends.json`. An unknown name raises `LocalAIError`. |
| `allow_cloud` | boolean | `true` | `false` skips cloud models (the prompt stays local). An explicit cloud model with `false` is an error. |
| `max_tokens` | integer | `1024` | Cap on generated tokens, 1-4096. The timeout grows with it (at least 30s, 90s for cloud models). |
| `temperature` | number | `0.2` | Sampling temperature, 0.0-2.0. |
| `enable_thinking` | boolean | `false` | Sent to every model. Reasoning models can spend their whole token budget "thinking" and return empty text; leave it off unless a prompt needs deeper reasoning, and expect it to cost more time and tokens. |

## Returns
`{text, model, backend, finish_reason}` — `model` is the model identifier the server reported,
`backend` is the entry that answered. A `max_tokens` too low for the answer still truncates normally:
`finish_reason: "length"`, partial text, not an error.

## Errors
Raises `LocalAIError` — bad arguments, an unknown, unconfigured or failed explicit model, an explicit
cloud model with `allow_cloud=False`, or every model failing under `auto`. The message never includes a
backend URL or key, so it is safe to show a caller. Nothing escalates to Claude automatically: retrying
with another `model` or escalating is the caller's decision.

## Configuration
Each entry in `local_ai_backends.json` names the environment variables it reads (`url_env`,
`model_env`, `key_env`), so the file itself holds no addresses or keys. An entry without its
address, model or key is simply skipped. See `docs/configuration.md` for the variables the shipped file
uses.

## Security
Same `MCP_AUTH_TOKEN` / `MCP_ALLOWED_HOSTS` protection as every other tool. Callers select a model by
name; they can never supply their own endpoint.

## Implementation
`tools/local_ai/ask.py`, registry `local_ai_backends.json`. Tests: `tests/test_local_ai_ask.py` (all HTTP
mocked; no model server needed).

## Machine-readable definition
`ask.json`, same directory.

## Related
- `reasoning.delegate` — decides whether a task should come here at all.
- `registry.find` — uses this as its last-resort fallback when exact and keyword matching find nothing.
