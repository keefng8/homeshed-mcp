"""tts.voices. See ../../capabilities/tts/voices.md."""
from __future__ import annotations

from registry import tool
from tools.tts import _eleven as e
from tools.tts import _google as g

PROVIDERS = ("google", "elevenlabs")


def provider_of(provider: str) -> str:
    p = (provider or "google").strip().lower()
    if p not in PROVIDERS:
        raise g.TTSError(f"provider is one of {list(PROVIDERS)}")
    return p


@tool(name="voices", category="tts", doc="tts/voices.md")
def voices(language: str = "", provider: str = "google", search: str = "") -> dict:
    """Voices for tts.synthesize: Google's Chirp 3 HD (default; asked at most once a day per language, free), or the
    owner's ElevenLabs account (provider="elevenlabs"; asked at most once an hour; listing is free).

    Args:
        language: Google only: a language code like en-GB, or just en; empty lists every language.
        provider: "google" (default) or "elevenlabs".
        search: ElevenLabs only: words to match in a voice's name, description or labels.

    Returns:
        Google: {"provider", "voices": [{"name", "gender", "language"}], "count"}
        ElevenLabs: {"provider", "voices": [{"voice_id", "name", "gender", "accent", "age", "use_case",
        "description", "category", "preview_url", "labels"}], "count"}
    """
    p = provider_of(provider)
    found = e.voices(search) if p == "elevenlabs" else g.voices(language)
    return {"provider": p, "voices": found, "count": len(found)}
