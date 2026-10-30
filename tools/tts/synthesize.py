"""tts.synthesize. See ../../capabilities/tts/synthesize.md."""
from __future__ import annotations

import base64
import binascii
import os
import re
import secrets
import time
from pathlib import Path

from paths import data_path
from registry import tool
from tools.tts import _count
from tools.tts import _eleven as e
from tools.tts import _google as g
from tools.tts.voices import provider_of

KEEP_DAYS = 7  # the station takes each piece straight away; a book's worth of MP3s mustn't pile up for ever
NAME_RE = re.compile(r"^tts-[a-z0-9-]{1,24}-\d{8}-\d{6}-[0-9a-f]{6}\.mp3$")

# MP3 frame headers (MPEG audio layer III): bitrates in kbit/s by index, sample rates in Hz, samples in a frame.
_BITRATES = {1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
             2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}  # by the header's version bits


def caller() -> str:
    import clients
    return clients.current_client.get() or "owner"


def output_dir() -> Path:
    return Path(os.environ.get("TTS_OUTPUT_DIR") or data_path("usage/tts"))


def slug(client: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", client.lower()).strip("-") or "app")[:24].strip("-") or "app"


def mp3_seconds(data: bytes) -> float | None:
    """The length of an MP3, from its frame headers (none of the audio is decoded). None if it isn't one."""
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:  # an ID3v2 tag first: its size is 4 bytes of 7 bits each
        i = 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
        i += 10 if data[5] & 0x10 else 0
    samples, rate, frames = 0, 0, 0
    while i + 4 <= len(data):
        b1, b2 = data[i + 1], data[i + 2]
        version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
        if data[i] != 0xFF or (b1 & 0xE0) != 0xE0 or version == 1 or layer != 1 or (b2 >> 4) in (0, 15) \
                or (b2 >> 2) & 3 == 3:
            if frames:
                break  # the audio has ended (a tag or padding after it)
            i += 1
            continue
        mpeg1 = version == 3
        rate = _RATES[version][(b2 >> 2) & 3]
        kbps = _BITRATES[1 if mpeg1 else 2][b2 >> 4]
        size = (144 if mpeg1 else 72) * kbps * 1000 // rate + ((b2 >> 1) & 1)
        samples += 1152 if mpeg1 else 576
        frames += 1
        i += size
    return round(samples / rate, 2) if frames else None


def _prune(folder: Path) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for f in folder.glob("tts-*.mp3"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except OSError:
        pass  # tidying never fails a call


def _save(audio: bytes, who: str) -> str:
    """Keep one MP3 for tts.fetch; its name."""
    folder = output_dir()
    folder.mkdir(parents=True, exist_ok=True)
    _prune(folder)
    name = f"tts-{slug(who)}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.mp3"
    (folder / name).write_bytes(audio)
    return name


@tool(name="synthesize", category="tts", doc="tts/synthesize.md")
def synthesize(text: str, voice: str = "", speaking_rate: float = 1.0, provider: str = "google",
               voice_id: str = "", model: str = "", stability: float | None = None,
               similarity_boost: float | None = None, speed: float | None = None, style: float | None = None,
               use_speaker_boost: bool | None = None, seed: int | None = None, previous_text: str = "",
               next_text: str = "", previous_request_ids: list | None = None, next_request_ids: list | None = None,
               output_format: str = "") -> dict:
    """Speak one piece of text as an MP3, saved on the tool server for tts.fetch. Google Chirp 3 HD by default:
    counted against Google's free 1 million characters a month (Pacific time months); a piece that would go past the
    month's cap is refused with TTS_CAP_REACHED, so nothing is charged unless the owner allowed paid characters.
    provider="elevenlabs" uses the owner's ElevenLabs plan instead (mp3_44100_128): a piece that would go past the
    plan's characters this period is refused with TTS_CAP_REACHED before anything is sent, so there's no overage.

    Args:
        provider: "google" (default) or "elevenlabs".
        voice_id: ElevenLabs only: a voice id from tts.voices(provider="elevenlabs") (voice= works too).
        model: ElevenLabs only: a model id; default eleven_multilingual_v2.
        stability, similarity_boost: ElevenLabs only, optional: 0 to 1 (ElevenLabs' defaults 0.5 and 0.75).
        speed: ElevenLabs only, optional: 0.7 to 1.2; 1.0 is normal.
        style: ElevenLabs only, optional: 0 to 1. use_speaker_boost: ElevenLabs only, optional: true or false.
        seed: ElevenLabs only, optional: 0 to 4294967295, for repeatable takes.
        previous_text, next_text: ElevenLabs only, optional: up to 1,000 characters of the text either side.
        previous_request_ids, next_request_ids: ElevenLabs only, optional: up to 3 request_ids tts.synthesize
            returned, so pieces join without seams.
        output_format: ElevenLabs only: mp3_44100_128 (default) or mp3_44100_192 (Creator tier and up).
        text: up to 5,000 bytes (Google's limit per request; about 5,000 characters of English, fewer with accents or
            other alphabets). Cut a book at sentence ends. Spaces and new lines count as characters.
        voice: a Chirp 3 HD voice name from tts.voices, e.g. en-GB-Chirp3-HD-Charon.
        speaking_rate: 0.25 to 2.0; 1.0 is the voice's normal pace.

    Returns:
        {"provider", "name", "bytes", "seconds", "seconds_from": "mp3 frames", "characters", "month", "month_characters",
         "free_left", "left"}. Take the MP3 with tts.fetch(name) within 7 days.
    """
    if not isinstance(text, str) or not text.strip():
        raise g.TTSError("text must be non-empty")
    if provider_of(provider) == "elevenlabs":
        audio, info = e.synthesize(text, (voice_id or voice or "").strip(), model, stability, similarity_boost, speed,
                                   style=style, use_speaker_boost=use_speaker_boost, seed=seed,
                                   previous_text=previous_text, next_text=next_text,
                                   previous_request_ids=previous_request_ids, next_request_ids=next_request_ids,
                                   output_format=output_format)
        seconds = mp3_seconds(audio) if audio else None
        if seconds is None:
            raise g.TTSError("ElevenLabs' answer had no MP3 audio in it")
        name = _save(audio, caller())
        return {"provider": "elevenlabs", "name": name, "bytes": len(audio), "seconds": seconds,
                "seconds_from": "mp3 frames", "characters": len(text), "model": info["model"],
                "request_id": info["request_id"], "output_format": info["output_format"],
                "left": max(0, info["left_before"] - len(text)), "limit": info["limit"], "resets": info["resets"],
                "left_note": "estimated from before this piece; tts.usage(provider=\"elevenlabs\") reads the exact figure"}
    size = len(text.encode("utf-8"))
    if size > g.MAX_INPUT_BYTES:
        raise g.TTSError(f"text is {size:,} bytes ({len(text):,} characters); Google takes at most "
                         f"{g.MAX_INPUT_BYTES:,} bytes a request, so cut it into smaller pieces at sentence ends")
    m = g.VOICE_RE.match(str(voice or "").strip())
    if not m:
        raise g.TTSError("voice must be a Chirp 3 HD voice name like en-GB-Chirp3-HD-Charon (tts.voices lists them)")
    if isinstance(speaking_rate, bool) or not isinstance(speaking_rate, (int, float)) \
            or not 0.25 <= float(speaking_rate) <= 2.0:
        raise g.TTSError("speaking_rate is a number from 0.25 to 2.0")
    g.ready()  # a missing or bad sign-in is said plainly before anything is counted
    who, chars = caller(), len(text)
    mon = _count.reserve(who, chars)
    try:
        body = g.request("POST", "/v1/text:synthesize", json={
            "input": {"text": text},
            "voice": {"languageCode": m.group(1), "name": voice.strip()},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": float(speaking_rate)}})
        try:
            audio = base64.b64decode(body.get("audioContent") or "", validate=True)
        except (binascii.Error, ValueError, TypeError):
            audio = b""
        seconds = mp3_seconds(audio) if audio else None
        if seconds is None:
            raise g.TTSError("Google's answer had no MP3 audio in it")
    except Exception:
        _count.give_back(who, chars, mon)
        raise
    name = _save(audio, who)
    s = _count.summary()
    return {"provider": "google", "name": name, "bytes": len(audio), "seconds": seconds, "seconds_from": "mp3 frames",
            "characters": chars, "month": s["month"], "month_characters": s["characters"],
            "free_left": s["free_left"], "left": s["left"]}
