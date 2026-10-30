# Text to speech — Speak a piece (Google Chirp 3 HD)

## ID
`tts.synthesize` (2026-10-28, to-do #151: audiobooks of the owner's books, about 700,000 characters each, made by the
station inside Google's free monthly allowance)

## Purpose
Speak one piece of text as an MP3 with a Google Chirp 3 HD voice. The file is kept on the tool server for 7 days;
take it with `tts.fetch`. The caller cuts the book into pieces at sentence ends, makes chapter files and joins them:
these tools hold the key, keep the files and count the characters.

## Parameters
| Name | Notes |
|---|---|
| `text` | Up to 5,000 bytes (Google's limit per request): about 5,000 characters of plain English, fewer with accents or other alphabets. Longer is refused before anything is sent or counted. Plain text: SSML and `[pause]` markup aren't passed on. |
| `voice` | A Chirp 3 HD name from `tts.voices`, e.g. `en-GB-Chirp3-HD-Charon`. The language comes from the name. |
| `speaking_rate` | 0.25 to 2.0, default 1.0 (the voice's normal pace). |

## Returns
`{"name", "bytes", "seconds", "seconds_from": "mp3 frames", "characters", "month", "month_characters", "free_left",
"left"}`. Google's answer doesn't give a length, so `seconds` is worked out from the MP3's frame headers.
`free_left` is what's left of the free million; `left` also counts the owner's paid allowance.

## The cap
- Every piece's characters (spaces and new lines too, as Google bills) are counted **before** it's sent, so two apps
  at once can't both slip under the cap, and given back if Google refused it.
- A piece that would take the month past **1,000,000 free + the owner's paid allowance** is refused with a reason
  starting **`TTS_CAP_REACHED`**, saying what's left and when the count starts again.
- The paid allowance is the owner's setting only: **Settings > Text to speech > paid characters a month**. Default
  **None**: free only, so the tools never cost money unless the owner chooses.
- Months are Google's billing months, **US Pacific time**: the count starts again at midnight Pacific on the 1st.
- Only this server's calls are counted. Other use of the same Google billing account isn't seen, so `free_left` is
  the most that's left, not a promise.

## Who can use it
The owner's sessions, and apps granted `tts.synthesize` on the API access page (it's a write tool: it can use up the
month's allowance). Each app's calls, characters and refusals are counted (`tts.usage`).

## Errors
Empty or over-long text; a voice that isn't a Chirp 3 HD name; speaking_rate out of range; no sign-in
(`GOOGLE_TTS_SERVICE_ACCOUNT`, see the guide *Google text to speech* on the Control Panel); `TTS_CAP_REACHED`; Google's own
refusal (HTTP status and a short reason, never the key or an address); Google too slow (120 s).

## Facts behind it (checked 2026-10-28 on Google's own pages)
| Fact | Source |
|---|---|
| Chirp 3 HD: 0 to 1 million characters a month free, then US$30 per million; spaces, new lines and SSML tags count; billing must be on | https://cloud.google.com/text-to-speech/pricing |
| 5,000 total bytes per request; Chirp 3 HD 200 requests a minute per project | https://docs.cloud.google.com/text-to-speech/quotas |
| Chirp 3 HD: MP3 for non-streaming requests; pace (`speaking_rate`) 0.25 to 2.0 in every language; SSML in Preview; `[pause]` tags only in the `markup` field; voice names like `en-US-Chirp3-HD-Kore` | https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd |
| A service account signs in with a signed JWT swapped at oauth2.googleapis.com/token for an access token (sent as `Authorization: Bearer`) | https://developers.google.com/identity/protocols/oauth2/service-account |
| Billing days and periods are US Pacific time | https://docs.cloud.google.com/billing/docs/how-to/reports, https://docs.cloud.google.com/identity/docs/how-to/understand-your-bills-and-charges |

Sign-in: the first real call with an API key (2026-10-28) got HTTP 401, "API keys are not supported by this API.
Expected OAuth2 access token". So the tools sign in as a service account (its JSON key file in the vault as
`GOOGLE_TTS_SERVICE_ACCOUNT`), with an API key (`GOOGLE_TTS_API_KEY`) only as a fallback for projects that accept one.

## ElevenLabs (provider="elevenlabs", 29 Oct 2026)
The owner's paid ElevenLabs plan, key `ELEVENLABS_API_KEY` in the vault. `voice_id` (or `voice`) from `tts.voices(provider="elevenlabs")`, `model` (default `eleven_multilingual_v2`), optional `stability` and `similarity_boost` (0 to 1) and `speed` (0.7 to 1.2). Up to 5,000 characters a piece; MP3 at `mp3_44100_128`, kept for `tts.fetch` like Google's. Before sending, the plan's characters left are read from ElevenLabs: a piece that would go past them is refused with `TTS_CAP_REACHED`, and nothing is sent, so the plan never goes into overage. Source: elevenlabs.io/docs/api-reference/text-to-speech/convert (checked 2026-10-29).
Continuity (passed to ElevenLabs as given): `seed`, `previous_text` / `next_text` (up to 1,000 characters each), `previous_request_ids` / `next_request_ids` (up to 3 `request_id`s this tool returned), `style` (0 to 1), `use_speaker_boost`, and `output_format` `mp3_44100_128` or `mp3_44100_192` (Creator tier and up). MP3 only, since `tts.fetch` hands out MP3s.
