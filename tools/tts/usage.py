"""tts.usage. See ../../capabilities/tts/usage.md."""
from __future__ import annotations

from registry import tool
from tools.tts import _count, _eleven
from tools.tts.voices import provider_of


@tool(name="usage", category="tts", doc="tts/usage.md")
def usage(provider: str = "google") -> dict:
    """This month's text-to-speech characters against Google's free allowance and the owner's paid allowance, when
    the count starts again, and each app's calls. Read-only; needs no Google key.

    Args:
        provider: "google" (default: this server's own count against Google's free million) or "elevenlabs" (the
            plan's own numbers, read from ElevenLabs: characters used, the limit, what's left and the reset).

    Returns:
        ElevenLabs: {"provider", "tier", "status", "characters", "limit", "left", "resets", "overage_possible", "note"}
        Google: {"month", "characters", "calls", "free_allowance", "free_left", "paid_allowance", "left", "resets",
         "paid_characters", "paid_usd_estimate", "clients": {app: {"calls", "characters", "refused"}}, "note"}
    """
    if provider_of(provider) == "elevenlabs":
        return {"provider": "elevenlabs", **_eleven.subscription(),
                "note": "From ElevenLabs itself, this billing period. tts.synthesize refuses a piece that would go "
                        "past the limit (TTS_CAP_REACHED), so the plan never goes into overage."}
    return {"provider": "google", **_count.summary(),
            "note": "Months are Google's billing months (US Pacific time). Only this server's calls are counted: other "
                    "use of the same Google account isn't seen. Paid characters are allowed only by the owner: "
                    "Settings > Text to speech."}
