"""notify.send. See ../../capabilities/notify/send.md."""
from __future__ import annotations

import logging
import os
import threading
import time

import httpx

import runtime_settings
from registry import tool

_VALID_PRIORITIES = {"min", "low", "default", "high", "urgent", "max"}

# Bundling (nexsift's idea, our own code): automated callers send more pushes, and only what matters should buzz the
# owner's phone. Per topic: high/urgent/max always go at once; the same push again within DEDUPE_S is dropped; past
# BURST sends in WINDOW_S the rest wait and go as one summary push when the window closes. In memory: a restart drops a
# waiting summary (at most WINDOW_S of non-urgent pushes), the known ceiling of this minimal version.
BURST, WINDOW_S, DEDUPE_S = 3, 600, 1800
URGENT = {"high", "urgent", "max"}
_lock = threading.Lock()
_sent: dict[str, list[float]] = {}            # topic -> send times inside the window
_seen: dict[tuple[str, str, str], float] = {}  # (topic, title, message) -> when it last went
_held: dict[str, list[tuple[str, str]]] = {}   # topic -> (title, message) waiting for the summary
log = logging.getLogger(__name__)


class NotifyError(RuntimeError):
    """Bad parameters, missing configuration, or the ntfy backend didn't answer cleanly."""


@tool(name="send", category="notify", doc="notify/send.md")
def send(
    message: str,
    topic: str = "",
    title: str | None = None,
    priority: str = "default",
    tags: list[str] | None = None,
) -> dict:
    """Send a push notification through your ntfy server. Real-world use:
    any tool (or Claude, directly) can call this to reach you — deploy done,
    health check failing, a long task finished. General-purpose, not wired into anything
    automatically yet; callers opt in explicitly.

    Args:
        message: the notification body. Required, non-empty.
        topic: which ntfy topic to publish to. Empty uses the ntfy_default_topic setting, then
            NTFY_DEFAULT_TOPIC, falling back to "mcp-server". Subscribe to that topic in your ntfy
            app, or notifications go nowhere.
        title: optional notification title, shown above the message.
        priority: one of "min", "low", "default", "high", "urgent"/"max". Default "default".
        tags: optional list of ntfy tag/emoji short-codes (e.g. ["warning", "computer"]) —
            see ntfy's own tag list for what renders as an emoji.

    Bundling: "high", "urgent" and "max" always go at once (use them for anything time-critical). Otherwise the same
    push again within 30 minutes is dropped, and after 3 pushes to a topic in 10 minutes the rest wait and go as one
    summary push when the 10 minutes are up.

    Returns:
        {"sent": True, "topic": str, "id": str, "message": str} — `id` is ntfy's own message id. A bundled push
        returns {"sent": False, "held": True, ...} (it goes in the summary) or {"sent": False, "dropped": why, ...}.

    Raises:
        NotifyError: empty message, invalid priority, NTFY_BASE_URL/NTFY_AUTH_TOKEN not
            configured, the ntfy backend unreachable, or a non-200 response (including 401/403
            if the token is stale). Never degrades silently — sending IS the point of the call,
            so a failure has to be a clear error, not a silent no-op.
    """
    if not message or not message.strip():
        raise NotifyError("message must be non-empty")
    if priority not in _VALID_PRIORITIES:
        raise NotifyError(f"priority must be one of {sorted(_VALID_PRIORITIES)}, got {priority!r}")

    topic = (topic.strip() or runtime_settings.get("ntfy_default_topic")
             or os.environ.get("NTFY_DEFAULT_TOPIC", "").strip() or "mcp-server")
    base_url = runtime_settings.address("NTFY_BASE_URL")  # Settings page first, then .env
    if not base_url:
        raise NotifyError("NTFY_BASE_URL not configured")
    import vault  # stored credentials first, then .env (vault.py)
    auth_token = vault.secret("NTFY_AUTH_TOKEN")
    if not auth_token:
        raise NotifyError("NTFY_AUTH_TOKEN not configured")

    now, key = time.time(), (topic, title or "", message.strip())
    if priority not in URGENT:
        with _lock:
            if now - _seen.get(key, 0) < DEDUPE_S:
                return {"sent": False, "dropped": "the same push went in the last 30 minutes", "topic": topic,
                        "message": message}
            recent = [t for t in _sent.get(topic, []) if now - t < WINDOW_S]
            _sent[topic] = recent
            if len(recent) >= BURST:
                _seen[key] = now
                _held.setdefault(topic, []).append((title or "", message.strip()))
                if len(_held[topic]) == 1:  # the first one held starts the summary's clock
                    wait = WINDOW_S - (now - recent[0])
                    threading.Timer(wait, _flush, args=(topic, base_url, auth_token)).start()
                return {"sent": False, "held": True, "topic": topic, "message": message,
                        "summary_in_s": round(WINDOW_S - (now - recent[0]))}
    out = _post(base_url, auth_token, topic, message, title, priority, tags)
    with _lock:
        _sent.setdefault(topic, []).append(now)
        _seen[key] = now
        if len(_seen) > 500:  # forget pushes past the dedupe window
            for k in [k for k, t in _seen.items() if now - t >= DEDUPE_S]:
                del _seen[k]
    return out


def _flush(topic: str, base_url: str, auth_token: str) -> None:
    """One summary push for everything held on `topic` (a Timer thread: logs, never raises)."""
    with _lock:
        held = _held.pop(topic, [])
    if not held:
        return
    lines = [f"- {t}: {m}" if t else f"- {m}" for t, m in held]
    body = "\n".join(line[:160] for line in lines[:10]) + (f"\n…and {len(lines) - 10} more" if len(lines) > 10 else "")
    try:
        _post(base_url, auth_token, topic, body, f"{len(held)} more update{'s' if len(held) != 1 else ''}", "default", None)
        with _lock:
            _sent.setdefault(topic, []).append(time.time())
    except NotifyError as exc:
        log.warning("notify summary for %s not sent: %s", topic, exc)


def _post(base_url: str, auth_token: str, topic: str, message: str, title: str | None, priority: str,
          tags: list[str] | None) -> dict:
    headers = {"Priority": priority, "Authorization": f"Bearer {auth_token}"}
    if title:
        headers["Title"] = title
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/{topic}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=8.0,
        )
    except httpx.RequestError as e:
        raise NotifyError(f"could not reach the ntfy backend: {e}") from None

    if resp.status_code != 200:
        raise NotifyError(f"ntfy backend returned http {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as e:
        raise NotifyError(f"unexpected response from the ntfy backend: {e}") from None

    return {"sent": True, "topic": topic, "id": payload.get("id"), "message": message}
