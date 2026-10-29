"""tts.usage. See ../../capabilities/tts/usage.md."""
from __future__ import annotations

from registry import tool
from tools.tts import _count


@tool(name="usage", category="tts", doc="tts/usage.md")
def usage() -> dict:
    """This month's text-to-speech characters against Google's free allowance and the owner's paid allowance, when
    the count starts again, and each app's calls. Read-only; needs no Google key.

    Returns:
        {"month", "characters", "calls", "free_allowance", "free_left", "paid_allowance", "left", "resets",
         "paid_characters", "paid_usd_estimate", "clients": {app: {"calls", "characters", "refused"}}, "note"}
    """
    return {**_count.summary(),
            "note": "Months are Google's billing months (US Pacific time). Only this server's calls are counted: other "
                    "use of the same Google account isn't seen. Paid characters are allowed only by the owner: "
                    "Settings > Text to speech."}
