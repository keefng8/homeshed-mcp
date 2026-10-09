"""Etsy Open API v3 for the etsy.* tools. See ../../capabilities/etsy/read.md.

Credentials by name only (vault.secret: the Control Panel's Credentials first, then the environment):
  ETSY_KEYSTRING      the app's keystring (Your Apps page)
  ETSY_SHARED_SECRET  the app's shared secret; since 2026-02-09 every request sends x-api-key: keystring:secret
  ETSY_REFRESH_TOKEN  the shop owner's OAuth 2.0 refresh token (scopes shops_r listings_r transactions_r)
  ETSY_SHOP_ID        the numeric shop id (not a secret)

Access tokens (1 hour) are kept in memory only. Etsy hands back a new refresh token with each refresh; it is written
back to the vault with set_credential (audited, actor "etsy-token-refresh"). If the vault can't take it (the vault
is off, or the token came from the environment), it's kept in memory for this process and every result says so.
Off unless the owner switches commerce_etsy_enabled on. Reads by default; write() changes one of the shop's listings,
only for the etsy.listing write tools (2026-10-08), each behind its own switch and a connection made with listings_w.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from tools.commerce import _http as h
from tools.commerce import vault_name

# The standard vault entry names (spelled out so they can be searched for, and the docs check can see them). The
# owner can name others in Settings > Online shops; the functions below read his choice on every call.
KEYSTRING, SHARED_SECRET = "ETSY_KEYSTRING", "ETSY_SHARED_SECRET"
REFRESH_TOKEN, SHOP_ID = "ETSY_REFRESH_TOKEN", "ETSY_SHOP_ID"


def _keystring() -> str:
    return vault_name("commerce_etsy_keystring_name")


def _shared_secret() -> str:
    return vault_name("commerce_etsy_shared_secret_name")


def _refresh_token() -> str:
    return vault_name("commerce_etsy_refresh_token_name")


def _shop_id_name() -> str:
    return vault_name("commerce_etsy_shop_id_name")


SETTING = "commerce_etsy_enabled"
TOKEN_PATH = "/v3/public/oauth/token"
REFRESH_ACTOR = "etsy-token-refresh"
EARLY_S = 120  # refresh this long before Etsy's expiry

_lock = threading.Lock()
_state: dict[str, Any] = {"access": None, "expires": 0.0, "refresh": None, "warning": None}


def _app_key() -> tuple[str, str]:
    kname, sname = _keystring(), _shared_secret()
    key, sec = h.secret(kname), h.secret(sname)
    missing = [n for n, v in ((kname, key), (sname, sec)) if not v]
    if missing:
        raise h.missing(missing, "Etsy")
    return key, sec


def shop_id() -> str:
    name = _shop_id_name()
    raw = h.secret(name)
    if not raw:
        raise h.missing([name], "Etsy")
    if not raw.isdigit():
        raise h.CommerceError(f"{name} should be the shop's number (it isn't shown)")
    return raw


def _save_refresh(new: str) -> str | None:
    """Write the rotated refresh token to the vault. Returns a warning, or None when saved."""
    import vault
    try:
        vault.set_credential(_refresh_token(), new, actor=REFRESH_ACTOR, kind="token", service="Etsy")
        return None
    except Exception as exc:  # noqa: BLE001 - VaultError or a disk problem: keep it in memory, say so
        kind = type(exc).__name__
        return (f"Etsy issued a new refresh token but the vault couldn't save it ({kind}); it's held in memory "
                f"until the tool server restarts. Re-authorise and store {_refresh_token()} again")


def _refresh(key: str, sec: str) -> str:
    stored = _state["refresh"] or h.secret(_refresh_token())
    if not stored:
        raise h.missing([_refresh_token()], "Etsy shop access")
    resp = h.request("etsy", "POST", TOKEN_PATH, headers={"x-api-key": f"{key}:{sec}"},
                     secrets=(key, sec, stored),
                     data={"grant_type": "refresh_token", "client_id": key, "refresh_token": stored})
    body = h.json_of("etsy", resp)
    access, new = body.get("access_token"), body.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise h.CommerceError("Etsy's token answer had no access token")
    _state["access"] = access
    _state["expires"] = time.time() + max(60, int(body.get("expires_in") or 3600)) - EARLY_S
    if isinstance(new, str) and new and new != stored:
        warn = _save_refresh(new)
        _state["refresh"] = new if warn else None
        _state["warning"] = warn
    return access


def _access(key: str, sec: str, force: bool = False) -> str:
    with _lock:
        if not force and _state["access"] and time.time() < _state["expires"]:
            return _state["access"]
        return _refresh(key, sec)


def forget() -> None:
    """Drop the in-memory tokens (tests; a re-authorised shop)."""
    with _lock:
        _state.update(access=None, expires=0.0, refresh=None, warning=None)


def get(path: str, params: dict | None = None, oauth: bool = True) -> Any:
    h.require(SETTING, "Etsy")
    key, sec = _app_key()
    headers = {"x-api-key": f"{key}:{sec}", "Accept": "application/json"}
    for attempt in (0, 1):
        token = _access(key, sec, force=attempt == 1) if oauth else None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            resp = h.request("etsy", "GET", path, headers=headers, params=params,
                             secrets=tuple(s for s in (key, sec, token, _state["refresh"]) if s))
            return h.json_of("etsy", resp)
        except h.CommerceError as exc:
            if oauth and attempt == 0 and "HTTP 401" in str(exc):
                continue  # the access token went stale early: refresh once and try again
            raise
    raise h.CommerceError("Etsy refused the refreshed token (HTTP 401)")


WRITE_SETTING = "commerce_etsy_write_enabled"
DEACTIVATE_SETTING = "commerce_etsy_deactivate_enabled"


def write(method: str, path: str, form: dict, setting: str, label: str, files: dict | None = None) -> Any:
    """A change to one of the shop's listings (PATCH, PUT, or a POST with files: multipart): the read switch, the
    calling tool's own switch, and a connection made with listings_w (Connect Etsy asks for it once a write switch is
    on). Etsy reads a list in a form as one comma-separated value (repeated keys keep only the last one:
    etsy/open-api discussion #1086), so lists are joined here."""
    h.require(SETTING, "Etsy")
    h.require(setting, label)
    key, sec = _app_key()
    body = {k: ",".join(map(str, v)) if isinstance(v, (list, tuple)) else
            ("true" if v else "false") if isinstance(v, bool) else v for k, v in form.items()}
    headers = {"x-api-key": f"{key}:{sec}", "Accept": "application/json"}
    for attempt in (0, 1):
        token = _access(key, sec, force=attempt == 1)
        headers["Authorization"] = f"Bearer {token}"
        try:
            resp = h.request("etsy", method, path, headers=headers, data=body, files=files,
                             secrets=tuple(s for s in (key, sec, token, _state["refresh"]) if s))
            return h.json_of("etsy", resp)
        except h.CommerceError as exc:
            if attempt == 0 and "HTTP 401" in str(exc):
                continue  # the access token went stale early: refresh once and try again
            if "HTTP 403" in str(exc):
                raise h.CommerceError(f"{exc}. If that's a missing scope: the shop was connected read-only; press "
                                      "Connect Etsy again (Settings > Online shops), which now asks for listing edits "
                                      "(listings_w)") from None
            raise
    raise h.CommerceError("Etsy refused the refreshed token (HTTP 401)")


def note(out: dict) -> dict:
    if _state.get("warning"):
        out["warning"] = _state["warning"]
    return out
