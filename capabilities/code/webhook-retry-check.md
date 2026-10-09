# code.webhook_retry_check

Finds webhook handlers that catch an error, log it and then answer 200 anyway. Payment providers (PayPal, Stripe and
most others) only retry a delivery that got a failure code back, so the event is lost for good, silently. This happened in a real PayPal webhook, where the lost event was a
paid membership.

## When to use it

Before shipping or changing any webhook receiver, especially payments.

## Inputs

| Input | Meaning |
|---|---|
| `folder` | The project folder to scan for `.js` files. It must be inside the folders this server may read (`READ_ALLOWED_ROOTS`, else the project folder the server runs in). |
| `handler_pattern` | Which functions to check: a case-insensitive pattern their name must match. Default `webhook`. |
| `exclude` | Extra folder names to skip. `node_modules`, `dist`, `build` and `.git` are always skipped. |
| `detail` | `compact` (the default): totals (`failed`, `counts` by id, `passed`), at most 20 failing rows and a few information rows; `more` says what was left out. `full`: every row. |

In Docker the server can read only its own folder unless you mount project folders and list them in `READ_ALLOWED_ROOTS`. On your own PC (a stdio install) it reads the project it was opened in.

## Returns

`{folder, checks, failed, handlers, meaning}`. Each check: `{id, ok, detail, file, line}`; `file` is relative to
`folder`. `meaning` explains each id once, and a failing row's `detail` names the function and the first 48 characters
of its catch block.

| id | ok | Meaning |
|---|---|---|
| `swallowed-error` | false | the catch block never signals failure, so the sender sees "delivered" |
| `signals-failure` | true | the catch sets `ctx.status` to 400 or more, calls `ctx.throw(...)`, re-throws, or calls `res.status(4xx/5xx)` |
| `no-try-catch` | true (info) | no try/catch, so an error becomes a 500 by default; this check can't see an error swallowed further in |

## Known limitations

- Brace matching and patterns over JavaScript text, not a parser. A failure signalled inside a nested callback, or by
  a helper the catch block calls, isn't seen.
- Only functions whose name matches `handler_pattern` are checked. Name another pattern if your handler is called
  something else (`handler_pattern: "paypal|stripe|notify"`).
- A handler with several try/catch blocks passes if any of them signals failure.
- Helpers whose name matches the pattern are checked too. A verifier like `verifyWebhook` that catches and returns
  `false` ("fail closed") is flagged, but it's correct when its caller turns `false` into a 4xx. In that case,
  narrow `handler_pattern` to the controller actions, or read the flagged helper's caller.
- `.js` only (no TypeScript yet), up to 5,000 files and 1 MB each.

## Risks

Read-only: it reads files and never runs them. It reads only inside the allowed folders; symlinks pointing outside
are skipped.
