"""tts.fetch. See ../../capabilities/tts/fetch.md."""
from __future__ import annotations

import base64
import re

from registry import tool
from tools.tts._google import TTSError
from tools.tts.synthesize import NAME_RE, caller, output_dir, slug

DEFAULT_MB, MAX_MB = 4.0, 20.0


@tool(name="fetch", category="tts", doc="tts/fetch.md")
def fetch(name: str, max_mb: float = DEFAULT_MB) -> dict:
    """An MP3 that tts.synthesize made, byte for byte, as base64, so the caller can save it.

    Args:
        name: the name tts.synthesize returned. An app gets only its own; the owner gets any.
        max_mb: the largest file returned (default 4 MB, up to 20). Base64 is a third bigger than the file.

    Returns:
        {"name", "audio_b64", "audio_mime": "audio/mpeg", "bytes"}
    """
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise TTSError("name must be one tts.synthesize returned (tts-...mp3)")
    if isinstance(max_mb, bool) or not isinstance(max_mb, (int, float)) or not 0 < max_mb <= MAX_MB:
        raise TTSError(f"max_mb is a number above 0, up to {MAX_MB:g}")
    who = caller()
    own = f"tts-{slug(who)}-"
    if who != "owner" and not (name.startswith(own) and re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}\.mp3", name[len(own):])):
        raise TTSError("that file was made by another app")
    path = output_dir() / name
    if not path.is_file():
        raise TTSError("no such file: it was never made, or it's over 7 days old and was tidied away")
    size = path.stat().st_size
    if size > max_mb * 1024 * 1024:
        raise TTSError(f"that file is {size / 1048576:.1f} MB, over max_mb; ask again with "
                       f"max_mb={min(MAX_MB, size / 1048576 + 0.1):.1f}")
    data = path.read_bytes()
    return {"name": name, "audio_b64": base64.b64encode(data).decode(), "audio_mime": "audio/mpeg", "bytes": len(data)}
