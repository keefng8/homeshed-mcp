# Text to speech — Voices

## ID
`tts.voices`

## Purpose
Google's Chirp 3 HD voices, to pick one for `tts.synthesize`. Only Chirp 3 HD voices are listed (the ones the free
million covers). Asked of Google at most once a day per language; listing voices costs nothing.

## Parameters
| Name | Notes |
|---|---|
| `language` | Optional: a code like `en-GB`, or `en` for every English one. Empty lists every language. |

## Returns
`{"voices": [{"name", "gender", "language"}], "count"}`, e.g. `{"name": "en-GB-Chirp3-HD-Charon", "gender": "male",
"language": "en-GB"}`.

## Errors
No sign-in (`GOOGLE_TTS_SERVICE_ACCOUNT`), a language that isn't a code, Google's own refusal.
