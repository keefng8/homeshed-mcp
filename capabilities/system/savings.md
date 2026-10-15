# system.savings

Tokens saved today and on each of the last 7 days, with what they'd have cost: for a dashboard's "saved" card and its
sparkline. Read-only.

## Inputs
| Name | Type | Default | What |
|---|---|---|---|
| `model` | string | `opus-5-5` | Whose API input price turns tokens into money: `opus-5-5`, `sonnet-5-5`, `haiku-5-5`, `fable-5-1` |

## Returns
```json
{"today": {"date": "2026-10-14", "saved_tokens": 54087, "commands": 448, "savings_pct": 47.6,
           "money": {"usd": 0.22, "gbp": 0.16, "at": "Claude Opus 5.5", "approximate": true}},
 "days": [{"date": "2026-10-12", "saved_tokens": 597}, {"date": "2026-10-13", "saved_tokens": 26123}, ...],
 "week": {"saved_tokens": 80807, "money": {...}},
 "tool_server_total_tokens": 7900,
 "source": "rtk"}
```
- `today` and `days` come from the command-output filter's daily counts on the helper PC (`GPU_SERVICE_URL`). Without
  one, or when it can't be reached, they are `null` / empty and `source` is `null`: never zeros.
- `today` is the newest day there is, so read its `date`.
- `tool_server_total_tokens` is this server's own estimate of what its tools saved, since its usage file began.
- Money is input tokens at the model's API price, converted at a fixed approximate rate (0.75 £ per $); the Control
  Panel uses the day's rate, so its figure can differ by a few pence.
