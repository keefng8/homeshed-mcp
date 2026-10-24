"""publisher.sign: ask for a read-only library script to be signed. See ../../capabilities/publisher/sign.md."""
from __future__ import annotations

import clients
import library_signing
from registry import tool


@tool(name="sign", category="publisher", doc="publisher/sign.md")
def sign(id: str, as_session: str = "") -> dict:
    """Ask for a read-only library script's current version to be signed with HomeShed's publisher key.

    Nothing is signed here: the request waits in the Control Panel's approvals bar, where the owner reads the code
    and says Yes. Runners that trust the key then run that exact version without asking.

    Args:
        id: the library script's id, e.g. "ops.disk-report". Its risk must be read.
        as_session: your own session's name, recorded as who asked.

    Returns:
        {"id", "version", "publisher", "status"}, or {"error"} (e.g. signing is off: no key in the vault yet).
    """
    try:
        return library_signing.request(id, as_session or clients.current_client.get() or "owner token")
    except library_signing.SigningError as exc:
        return {"error": str(exc)}
