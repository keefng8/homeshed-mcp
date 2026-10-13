# Spend — Set a delegated cap

## ID
`spend.cap_set`

## Purpose
Lets the owner's own AI sessions (for example a coordinating app) set an app's daily cap on paid images and
language-model calls, without the owner having to pass the number on. Added 2026-10-11.

## Limits
- Owner sessions only: an app's own key is refused.
- Only apps the owner opened to it (`clients.set_cap_delegated(name, True)`; `cap_delegated` in the client list).
  Every other app keeps the cap the owner set on the Control Panel (API access > Spend cap).
- 0 to 5 dollars a day. A higher cap is the owner's own call, on the Control Panel.
- Every change is in the API access audit ("spend cap changed").

## Parameters
| Name | Required | Notes |
|---|---|---|
| `client` | yes | The app's name, as `spend.today` lists it. |
| `usd` | yes | Dollars a day, 0-5. 0 leaves the app local and free tools only. |

## Returns
`{"client", "cap_usd", "spent_today": {"image", "llm", "total"}, "left_usd"}`.

## Related
- `spend.today`: what each app has spent today against its cap.
