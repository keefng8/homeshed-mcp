# Pinterest — pins and boards

## IDs
`pinterest.status`, `pinterest.boards.list`, `pinterest.board.create`, `pinterest.pin.create` (2026-10-10, the owner's
decision; R&D's scope: `researchandimprovements/pinterest-scope-review.md`)

## Before the first use
1. The owner makes a free Pinterest **business** account (Pinterest requires one for an API app), then an app at
   developers.pinterest.com (My apps > Connect app).
2. Save the app's ID and secret key under Keys and passwords as `PINTEREST_APP_ID` and `PINTEREST_APP_SECRET`.
3. In the app's settings on Pinterest, add the Control Panel's return address (shown on the Connect Pinterest card).
4. Settings > Shops and social > Pinterest: switch on "Let apps ask to pin to Pinterest", then press
   **Connect Pinterest** and allow it.

**Trial and Standard access.** A new app has Trial access: pins and boards are made in Pinterest's **sandbox**
(`api-sandbox.pinterest.com`) and only the owner sees them, on their own profile. That's enough to test, and to record
the screencast Pinterest's review asks for (the sign-in and a pin being made). Once Pinterest grants Standard access,
switch off "Pinterest: Trial access" and press Connect Pinterest again: a sandbox token doesn't work for public pins,
and the other way round, so each keeps its own (`PINTEREST_SANDBOX_*` and `PINTEREST_*` in the vault). A request asked
in one and approved after a switch to the other is refused ("ask again").

## pinterest.pin.create
**A call never pins anything.** It's checked, then waits in the approvals bar ("Pinterest pin"), with the image as a
thumbnail and "Read the full new text" showing the title, description, link, board and alt text. Pinterest's developer
guidelines say the person chooses each pin, so **no app's own approval and no AUTO mode can approve one**: only the
owner, one pin at a time.

| Name | Notes |
|---|---|
| `image` | `"folder/name.png"` in the image store, an image name, or a Printify mockup address. JPEG or PNG, up to 10 MB. 2:3 portrait (1000x1500) is what Pinterest recommends. |
| `title` | Up to 100 characters (Pinterest's limit). |
| `description` | Up to 800 characters (Pinterest's limit), including the AI line. |
| `link` | One of the shop's own **active** Etsy listings, `https://www.etsy.com/listing/<number>` (checked against the shop when asked and again on approval). Sent as the plain listing address. |
| `board` | The board's id or exact name (`pinterest.boards.list`). |
| `ai_generated` | Required. True when AI made or changed any of the image or words. |
| `alt_text` | What the image shows, up to 500 characters. |

- **AI disclosure**: with `ai_generated`, the description ends "AI-assisted design." (unless it says so already) and
  the pin carries Pinterest's own AI field, `ai_disclosures: {"values": ["AI_MODIFIED"]}` (API v5 5.28.0's pins/create;
  checked 2026-10-10).
- **No repeats**: the same image with the same link within 30 days is refused (a spam signal), and so is any pin
  of a listing pinned in the last 7 days, whatever the image, or one already waiting for the owner (a terms check,
  2026-10-10). Checked when asked and again on approval.
- **The image is fingerprinted** (sha256) when asked; on approval it's fetched again, and if it changed the approval
  fails ("decline this and ask again"). It goes to Pinterest as `image_base64`.
- **Capped**: `PINTEREST_DAILY_MAX` approved pins in 24 hours (default 5). Over it, the approval waits.
- **Nothing read from Pinterest is stored** (its developer guidelines): boards, account and pin details are read live
  on each call and never cached. Only what we made is kept, in `usage/pinterest_pins.json`: the new pin's id, our
  image's fingerprint, our listing link, the time, sandbox and the AI flag, for the repeat checks; plus the audit log.
  A waiting request carries the chosen board's id and name until the owner decides it (24 hours at most).

Returns `{"pending": true, "id", "pinterest_pin": summary, "expires_in_h", "how"}`. On approval:
`{"pin_id", "url", "sandbox", "note"}`.

## pinterest.board.create
Asks to make a board (`name` up to 100 characters, `description` up to 500, `privacy` PUBLIC or SECRET); it waits for
the owner's approval the same way. A name the account already has is refused; on approval a board made meanwhile is
never made twice.

## pinterest.boards.list, pinterest.status
Read-only: the boards (id, name, privacy, pin count); the connection, Trial or Standard, the account, pins in the last
24 hours and the daily limit.
