"""Connect an Etsy shop: OAuth 2.0 with PKCE, started and finished from the Control Panel (owner-only admin routes in
server.py). The owner, 2026-10-06: "add a Connect Etsy button ... and it can be public use too on release".

Etsy (developers.etsy.com, Authentication): the authorize page is www.etsy.com/oauth/connect; PKCE (S256) is
mandatory; the return address must be https and registered exactly in the app's settings; no client secret in the
code exchange; the access token's numeric prefix is the user id; refresh tokens last 90 days.

Two ways back: Etsy sends the browser to the panel's own https address (/api/etsy/callback), or, when the panel isn't
on https, to any https address the owner registered, and he pastes that address into the panel (finish_url).
Saved on success: the refresh token (ETSY_REFRESH_TOKEN, or the name set in Settings > Online shops) and the shop's
number (ETSY_SHOP_ID). Neither, nor the code or verifier, is ever returned or logged.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from paths import data_path
from tools.commerce import _http as h
from tools.etsy import _client as etsy

GRANTED_FILE = Path(os.environ.get("ETSY_CONNECTION_FILE") or data_path("usage/etsy_connection.json"))
AUTHORIZE_URL = "https://www.etsy.com/oauth/connect"
SCOPES = "shops_r listings_r transactions_r"
WRITE_SCOPE = "listings_w"  # asked for only while a listing write switch is on (least privilege, 2026-10-08)


def scopes() -> str:
    """What Connect Etsy asks for: read-only, plus listing edits when the owner has switched a write tool on."""
    on = any(h.enabled(s) for s in (etsy.WRITE_SETTING, etsy.DEACTIVATE_SETTING))
    return f"{SCOPES} {WRITE_SCOPE}" if on else SCOPES
PENDING_S = 600
MAX_PENDING = 5
ACTOR = "etsy-connect"
_lock = threading.Lock()
_pending: dict[str, dict] = {}  # state -> {"verifier", "redirect_uri", "expires"}


def _check_redirect(uri: str) -> str:
    uri = (uri or "").strip()
    parts = urlsplit(uri)
    if parts.scheme != "https" or not parts.hostname or parts.fragment or parts.username or parts.password:
        raise h.CommerceError("the return address must be a full https:// address (Etsy refuses anything else)")
    if len(uri) > 300:
        raise h.CommerceError("the return address is too long")
    return uri


def start(redirect_uri: str) -> dict:
    """Etsy's sign-in address for the owner to open, valid for 10 minutes."""
    key, _ = etsy._app_key()  # names the missing keystring or secret before anything starts
    redirect_uri = _check_redirect(redirect_uri)
    verifier = secrets.token_urlsafe(48)  # 64 characters, inside PKCE's 43-128
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for s in [s for s, p in _pending.items() if p["expires"] < now]:
            _pending.pop(s)
        while len(_pending) >= MAX_PENDING:
            _pending.pop(min(_pending, key=lambda s: _pending[s]["expires"]))
        _pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "expires": now + PENDING_S,
                           "scopes": scopes()}
    url = AUTHORIZE_URL + "?" + urlencode({"response_type": "code", "client_id": key, "redirect_uri": redirect_uri,
                                           "scope": scopes(), "state": state, "code_challenge": challenge,
                                           "code_challenge_method": "S256"})
    return {"url": url, "redirect_uri": redirect_uri, "expires_in_s": PENDING_S}


def finish_url(pasted: str) -> dict:
    """The address Etsy sent the browser to, pasted by the owner."""
    q = parse_qs(urlsplit((pasted or "").strip()).query)
    if q.get("error"):
        raise h.CommerceError(f"Etsy said: {h.clean(q.get('error_description', q['error'])[0])}")
    code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
    if not code or not state:
        raise h.CommerceError("that address has no code from Etsy: paste the whole address Etsy sent you to")
    return finish(code, state)


def finish(code: str, state: str) -> dict:
    """Swap Etsy's one-time code for tokens, save the refresh token and the shop's number."""
    with _lock:
        pend = _pending.pop(state or "", None)  # single use, whatever happens next
    if not pend:
        raise h.CommerceError("this sign-in isn't one the panel started, or it was already used: press Connect again")
    if pend["expires"] < time.time():
        raise h.CommerceError("the sign-in took longer than 10 minutes: press Connect again")
    key, sec = etsy._app_key()
    resp = h.request("etsy", "POST", etsy.TOKEN_PATH, headers={"x-api-key": f"{key}:{sec}"},
                     secrets=(key, sec, code, pend["verifier"]),
                     data={"grant_type": "authorization_code", "client_id": key, "redirect_uri": pend["redirect_uri"],
                           "code": code, "code_verifier": pend["verifier"]})
    body = h.json_of("etsy", resp)
    access, refresh = body.get("access_token"), body.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str) or not access or not refresh:
        raise h.CommerceError("Etsy's answer had no tokens")
    user_id = access.split(".", 1)[0]
    if not user_id.isdigit():
        raise h.CommerceError("Etsy's access token didn't carry a user number")
    import vault
    write = WRITE_SCOPE in pend.get("scopes", "")
    vault.set_credential(etsy._refresh_token(), refresh, actor=ACTOR, kind="token", service="Etsy",
                         label="Etsy shop connection" + (" (with listing edits)" if write else ""),
                         used_by=["etsy.* tools" + ("" if write else " (read-only)")])
    _save_granted(pend.get("scopes") or SCOPES)
    etsy.forget()  # the next call uses the new connection
    shop = _shop(key, sec, access, user_id)
    if shop.get("shop_id"):
        _save_shop_id(shop["shop_id"])
    return {"connected": True, "shop_name": shop.get("shop_name"), "shop_id_saved": bool(shop.get("shop_id")),
            "note": shop.get("note")}


def _save_granted(scopes_asked: str) -> None:
    """What the current connection was made with (not a secret): the panel then says when listing edits need a
    reconnect."""
    GRANTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    GRANTED_FILE.write_text(json.dumps({"scopes": scopes_asked, "at": time.time()}), encoding="utf-8")


def granted() -> str | None:
    """The scopes the current connection was asked with, or None when it's from before 2026-10-08 (read-only)."""
    try:
        return str(json.loads(GRANTED_FILE.read_text(encoding="utf-8")).get("scopes") or "") or None
    except (OSError, ValueError, AttributeError):
        return None


def _save_shop_id(shop_id: int) -> None:
    import vault
    vault.set_credential(etsy._shop_id_name(), str(shop_id), actor=ACTOR, kind="other", service="Etsy",
                         label="Etsy shop number")


def fill_shop_id() -> str | None:
    """Connected but no shop number saved (a connection from before 2026-10-06's fix): look it up once and save it.
    Returns a note for the panel, or None when there was nothing to do."""
    if h.secret(etsy._shop_id_name()) or not h.secret(etsy._refresh_token()):
        return None
    try:
        key, sec = etsy._app_key()
        with etsy._lock:
            access = etsy._refresh(key, sec)
        shop = _shop(key, sec, access, access.split(".", 1)[0])
    except h.CommerceError as exc:
        return f"couldn't look up the shop number ({exc})"
    if not shop.get("shop_id"):
        return shop.get("note")
    _save_shop_id(shop["shop_id"])
    return f"saved the shop number for {shop.get('shop_name') or 'your shop'}"


def _shop(key: str, sec: str, access: str, user_id: str) -> dict:
    """The signed-in user's shop. A user with no shop is connected, but there's nothing to read yet."""
    try:
        resp = h.request("etsy", "GET", f"/v3/application/users/{user_id}/shops",
                         headers={"x-api-key": f"{key}:{sec}", "Authorization": f"Bearer {access}",
                                  "Accept": "application/json"}, secrets=(key, sec, access))
        body = h.json_of("etsy", resp)
    except h.CommerceError as exc:
        return {"note": f"connected, but the shop couldn't be looked up ({exc}); set the shop number by hand"}
    sid = body.get("shop_id") if isinstance(body, dict) else None
    if not isinstance(sid, int):
        return {"note": "connected, but this Etsy account has no shop"}
    return {"shop_id": sid, "shop_name": h.trim(body.get("shop_name"), 80)}


def status() -> dict:
    """What the panel's Connect Etsy card shows. Names and yes/no only."""
    names = {"keystring": etsy._keystring(), "shared_secret": etsy._shared_secret(),
             "refresh_token": etsy._refresh_token(), "shop_id": etsy._shop_id_name()}
    filled = fill_shop_id()
    have = {k: bool(h.secret(n)) for k, n in names.items()}
    wanted = scopes()
    reconnect = have["refresh_token"] and WRITE_SCOPE in wanted and WRITE_SCOPE not in (granted() or "")
    return {"names": names, "have": have, "app_ready": have["keystring"] and have["shared_secret"],
            "connected": have["refresh_token"], "enabled": h.enabled(etsy.SETTING), "scopes": wanted,
            "reconnect_for_edits": bool(reconnect),
            **({"note": filled} if filled else {})}
