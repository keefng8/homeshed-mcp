"""HomeShed Pro: this install's connection to the Pro membership (HomeShed Pro is part of the Mavis Pro membership).

The owner makes an access key on the Pro website (the account page, Access keys: a `mav_` key, shown once) and pastes
it into HomeShed: the Pro card on the panel, or `homeshed-mcp packs` on the command line. No password is ever typed
into HomeShed: anyone can fork it, so a copy asking for a password could be phishing. The key is checked with the site
first and kept only if the site knows it, in the vault as MAVIS_PRO_TOKEN. Revoking it on the website ends this
install's access; disconnecting here forgets it.

HomeShed talks to the Pro site only once connected (or while connecting), and only when something needs Pro:
- status(): GET /api/pro-api/whoami, at most every 10 minutes (200: Pro; 403: a valid key whose membership has ended;
  401: revoked);
- packs() / pack(slug): the rule packs on offer, and one pack's content;
- the known-bugs feed (tools/bugs/pro_sync.py).
Nothing is sent anywhere before the owner connects.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

import httpx

import runtime_settings
import vault
from paths import data_path

# The Pro website's backend, where memberships and keys live.
DEFAULT_URL = "https://api.mavis-ai.co.uk"
TOKEN = "MAVIS_PRO_TOKEN"
# Client id and account name: nothing secret. Under usage/, a volume: at /data's top it lived in the container and every
# redeploy forgot it (found 2026-09-29; the token itself is in the vault, which was always a volume).
STATE_FILE = os.environ.get("MAVIS_PRO_STATE") or data_path("usage/mavis_pro.json")
CACHE_S = 600
_cache: dict = {}
_lock = threading.Lock()


class MavisProError(RuntimeError):
    """A message that is safe to show on the page: never a password or a token."""


def base_url() -> str:
    """The site's address, without a trailing /api (either form may be typed into Settings). One the Pro key mustn't go
    to (plain http to someone else's network, a user:password part...: rule_packs.address_problem) is ignored for the
    official site, where the key belongs (R&D's security review, 2026-10-01)."""
    url = (runtime_settings.address("MAVIS_PRO_URL") or DEFAULT_URL).rstrip("/")
    url = url[:-4] if url.endswith("/api") else url
    if url != DEFAULT_URL:
        from rule_packs import address_problem
        if problem := address_problem(url):
            import logging
            logging.getLogger("homeshed").warning("MAVIS_PRO_URL ignored, the official Pro site is used: %s", problem)
            return DEFAULT_URL
    return url


def api_url() -> str:
    """Where the backend's routes live: sign-in, access keys and the Pro API all sit under /api."""
    return base_url() + "/api"


def _state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    tmp = STATE_FILE + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, STATE_FILE)


def _message(resp: httpx.Response) -> str:
    """Strapi's own error text (it never echoes a password), shortened."""
    try:
        err = resp.json().get("error") or {}
        return str(err.get("message") or f"HTTP {resp.status_code}")[:200]
    except (ValueError, AttributeError):
        return f"HTTP {resp.status_code}"


KEY_RE = re.compile(r"^mav_[A-Za-z0-9_-]{20,200}$")


def connect_key(key: str) -> dict:
    """Checks a pasted Pro key with the site and keeps it only if the site knows it. Never echoes the key."""
    key = (key or "").strip()
    if not KEY_RE.match(key):
        raise MavisProError("That isn't a Pro key. Make one on the Pro website (your account page, Access keys): "
                            "it starts with mav_.")
    try:
        r = httpx.get(f"{api_url()}/pro-api/whoami", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except httpx.HTTPError:
        raise MavisProError("Couldn't reach the Pro website to check the key. Try again in a minute.") from None
    if r.status_code == 401:
        raise MavisProError("The Pro website doesn't know that key: it was mistyped, or revoked. Make a new one on "
                            "your account page.")
    if r.status_code not in (200, 403):
        raise MavisProError(f"The Pro website couldn't check the key just now (HTTP {r.status_code}). Try again shortly.")
    try:
        who = r.json() or {}
    except ValueError:
        who = {}
    client = who.get("client") if isinstance(who, dict) else None
    name = (client.get("name") if isinstance(client, dict) else client) or "Pro key"
    try:
        vault.set_credential(TOKEN, key, actor="pro-connect", label="HomeShed Pro: this install", kind="api-key",
                             service="homeshed-pro")
    except vault.VaultError:  # the vault is off (no key): say how to switch it on, never "send JSON" (a ValueError)
        raise MavisProError("The key is good, but this install can't store it yet: its vault has no key. On this "
                            "machine run  homeshed-mcp pro connect  (it sets one up); on a Docker or HTTP install, "
                            "run  homeshed-mcp init  and restart HomeShed.") from None
    _save_state({"client_name": str(name)[:80], "connected_at": time.time()})
    with _lock:
        _cache.clear()
    return status(fresh=True)


def status(fresh: bool = False) -> dict:
    """{connected, pro, account, client_name, connected_at, reason, unreachable}. Asks the backend at most every
    10 minutes; unreachable is set when the site gave no answer about the membership (down, or an HTTP error)."""
    token, state = vault.secret(TOKEN), _state()
    if not token:
        return {"connected": False, "pro": False, "address": base_url()}
    with _lock:
        hit = _cache.get("status")
        if hit and not fresh and time.time() - hit[0] < CACHE_S:
            return hit[1]
    out = {"connected": True, "pro": False, "account": state.get("account"), "client_name": state.get("client_name"),
           "connected_at": state.get("connected_at"), "address": base_url()}
    try:
        r = httpx.get(f"{api_url()}/pro-api/whoami", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if r.status_code == 200:
            out["pro"] = True
        elif r.status_code == 403:
            out["reason"] = "Your Pro membership has ended, so Pro features are paused here. Renew on the Pro website."
        elif r.status_code == 401:
            out.update(connected=False, reason="This install's Pro key was revoked or deleted on the Pro website. "
                                               "Paste a new one.")
        else:  # not an answer about the membership: pro.check keeps a recent one for 7 days (pro.GRACE_S)
            out.update(unreachable=True, reason=f"The Pro website answered HTTP {r.status_code}.")
    except httpx.HTTPError:
        out.update(unreachable=True, reason="Couldn't reach the Pro website just now.")
    with _lock:
        _cache["status"] = (time.time(), out)
    return out


PACK_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


# The most the Pro API's answer may be: a pack (256 KB at most) plus Strapi's envelope. Counted as it arrives, after
# decompression, so a huge or zip-bomb answer is refused before it fills memory (R&D's security review, 2026-10-01: the
# size was checked only once the whole body had been read).
PRO_MAX_BYTES = 512_000


def _pro_get(path: str) -> httpx.Response:
    token = vault.secret(TOKEN)
    if not token:
        raise MavisProError("HomeShed Pro isn't connected here: paste your Pro key on the Pro page.")
    try:
        with httpx.stream("GET", f"{api_url()}/pro-api{path}", headers={"Authorization": f"Bearer {token}"},
                          timeout=15) as streamed:
            body, size = [], 0
            for chunk in streamed.iter_bytes():
                size += len(chunk)
                if size > PRO_MAX_BYTES:
                    raise MavisProError("The Pro website sent more than a pack can be, so it was refused.")
                body.append(chunk)
            r = httpx.Response(streamed.status_code, content=b"".join(body),
                               headers={"content-type": streamed.headers.get("content-type", "application/json")})
    except httpx.HTTPError:
        raise MavisProError("The Pro website isn't reachable right now. Try again in a minute.") from None
    if r.status_code in (401, 403):
        raise MavisProError("The Pro website refused this install's key. Check your membership is active, or "
                            "paste a new key on the Pro page.")
    if r.status_code == 404:
        raise MavisProError("HomeShed Pro has no pack with that name.")
    if r.status_code != 200:
        raise MavisProError(f"The Pro website answered HTTP {r.status_code}.")
    return r


def packs() -> dict:
    """The rule packs HomeShed Pro offers, asked with this install's key. {configured, available: [...], error}."""
    if not vault.secret(TOKEN):
        return {"configured": False, "available": [], "error": None}
    try:
        rows = (_pro_get("/packs").json() or {}).get("data") or []
    except (MavisProError, ValueError) as exc:
        return {"configured": True, "available": [], "error": str(exc) if isinstance(exc, MavisProError)
                else "The Pro website's pack list was unreadable."}
    return {"configured": True, "error": None, "available": [
        {k: r.get(k) for k in ("slug", "title", "description")} for r in rows if isinstance(r, dict) and r.get("slug")]}


def pack(slug: str) -> dict:
    """One pack's content, size-checked and read as strict JSON. Whoever installs it checks it fully first
    (guard_engine.validate_pack and rule_packs' timed trial)."""
    import guard_engine
    if not PACK_SLUG.match(str(slug or "")):
        raise MavisProError("Unknown pack.")
    r = _pro_get(f"/packs/{slug}")
    if len(r.content) > 2 * guard_engine.MAX_PACK_BYTES:  # the pack plus Strapi's envelope (R&D's review)
        raise MavisProError("The Pro website sent a pack that's too big to be one.")
    try:
        content = ((r.json() or {}).get("data") or {}).get("content")
        found = guard_engine.strict_loads(content) if isinstance(content, str) else content
    except (ValueError, AttributeError):
        raise MavisProError("The Pro website's pack was unreadable.") from None
    if not isinstance(found, dict):
        raise MavisProError("The Pro website sent an empty pack.")
    return found


def disconnect() -> dict:
    """Forgets the key here. It stays on the account until it's revoked on the Pro website."""
    try:
        vault.delete_credential(TOKEN, actor="homeshed-pro-disconnect")
    except vault.VaultError:
        pass  # not in the vault (an older setup keeps it in .env, which only its owner can edit)
    name = _state().get("client_name")
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass
    with _lock:
        _cache.clear()
    return {"connected": False, "note": (f'The key "{name}" still exists on your account: revoke it on the Pro website '
                                         "(your account page, Access keys) if you won't use it again.") if name else None}
