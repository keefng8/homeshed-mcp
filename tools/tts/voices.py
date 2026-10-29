"""tts.voices. See ../../capabilities/tts/voices.md."""
from __future__ import annotations

from registry import tool
from tools.tts import _google as g


@tool(name="voices", category="tts", doc="tts/voices.md")
def voices(language: str = "") -> dict:
    """Google's Chirp 3 HD voices for tts.synthesize. Asked of Google at most once a day per language; free.

    Args:
        language: a language code like en-GB, or just en; empty lists every language.

    Returns:
        {"voices": [{"name", "gender", "language"}], "count"}
    """
    found = g.voices(language)
    return {"voices": found, "count": len(found)}
