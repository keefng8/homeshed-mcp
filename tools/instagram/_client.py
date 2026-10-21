"""Instagram (Meta) for instagram.* and the Control Panel's Connect Instagram card, the same shape as Threads
(tools/threads/_client.py). The owner, 2026-10-20: the Promoter posts to Instagram as well as Threads.

Meta's "Instagram API with Instagram Login" (developers.facebook.com/docs/instagram-platform): a professional account
(Business or Creator) signs in at instagram.com/oauth/authorize (scopes instagram_business_basic and
instagram_business_content_publish); the code (strip a trailing "#_") is swapped at api.instagram.com/oauth/access_token
with the Instagram app id AND secret for a 1-hour token and the user id; that is swapped at graph.instagram.com
(ig_exchange_token) for a 60-day token, refreshable (ig_refresh_token) once it is at least 24 hours old.

Kept in the vault: INSTAGRAM_ACCESS_TOKEN (the 60-day token) and INSTAGRAM_USER_ID. The app id and secret are read by
the names set in Settings > Shops and social. Nothing here returns or logs a token or the secret.
"""
from __future__ import annotations

import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from tools.commerce import _http as h
from tools.commerce import vault_name

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
SCOPES = "instagram_business_basic,instagram_business_content_publish"
API = "/v21.0"
TOKEN, USER = "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID"
SETTING = "social_instagram_enabled"
ACTOR = "instagram-connect"
PENDING_S, MAX_PENDING = 600, 5
REFRESH_AFTER_S = 7 * 86400
_lock = threading.Lock()
_pending: dict[str, dict] = {}


def _app() -> tuple[str, str]:
    iname, sname = vault_name("social_instagram_app_id_name"), vault_name("social_instagram_app_secret_name")
    app_id, secret = h.secret(iname), h.secret(sname)
    missing = [n for n, v in ((iname, app_id), (sname, secret)) if not v]
    if missing:
        raise h.missing(missing, "Instagram")
    return app_id, secret


def _check_redirect(uri: str) -> str:
    uri = (uri or "").strip()
    parts = urlsplit(uri)
    if parts.scheme != "https" or not parts.hostname or parts.fragment or parts.username or parts.password or len(uri) > 300:
        raise h.CommerceError("the return address must be a full https:// address (Meta refuses anything else)")
    return uri


def start(redirect_uri: str) -> dict:
    app_id, _ = _app()
    redirect_uri = _check_redirect(redirect_uri)
    state, now = secrets.token_urlsafe(24), time.time()
    with _lock:
        for s in [s for s, p in _pending.items() if p["expires"] < now]:
            _pending.pop(s)
        while len(_pending) >= MAX_PENDING:
            _pending.pop(min(_pending, key=lambda s: _pending[s]["expires"]))
        _pending[state] = {"redirect_uri": redirect_uri, "expires": now + PENDING_S}
    url = AUTHORIZE_URL + "?" + urlencode({"client_id": app_id, "redirect_uri": redirect_uri, "scope": SCOPES,
                                           "response_type": "code", "state": state})
    return {"url": url, "redirect_uri": redirect_uri, "expires_in_s": PENDING_S}


def finish_url(pasted: str) -> dict:
    q = parse_qs(urlsplit((pasted or "").strip().split("#", 1)[0]).query)
    if q.get("error"):
        raise h.CommerceError(f"Instagram said: {h.clean(q.get('error_description', q['error'])[0])}")
    code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
    if not code or not state:
        raise h.CommerceError("that address has no code from Instagram: paste the whole address it sent you to")
    return finish(code, state)


def finish(code: str, state: str) -> dict:
    with _lock:
        pend = _pending.pop(state or "", None)
    if not pend:
        raise h.CommerceError("this sign-in isn't one the panel started, or it was already used: press Connect again")
    if pend["expires"] < time.time():
        raise h.CommerceError("the sign-in took longer than 10 minutes: press Connect again")
    code = (code or "").removesuffix("#_")
    app_id, secret = _app()
    short = h.json_of("instagram-auth", h.request(
        "instagram-auth", "POST", "/oauth/access_token", headers={}, secrets=(secret, code),
        data={"client_id": app_id, "client_secret": secret, "code": code, "grant_type": "authorization_code",
              "redirect_uri": pend["redirect_uri"]}))
    if isinstance(short.get("data"), list) and short["data"]:  # some API versions wrap the answer in data[]
        short = short["data"][0]
    s_token, user_id = short.get("access_token"), str(short.get("user_id") or "")
    if not isinstance(s_token, str) or not s_token or not user_id.isdigit():
        raise h.CommerceError("Instagram's answer had no token or user id")
    long = h.json_of("instagram", h.request(
        "instagram", "GET", "/access_token", headers={}, secrets=(secret, s_token),
        params={"grant_type": "ig_exchange_token", "client_secret": secret, "access_token": s_token}))
    token = long.get("access_token")
    if not isinstance(token, str) or not token:
        raise h.CommerceError("Instagram didn't give a long-lived token")
    me = _me(token)
    if str(me.get("user_id") or "").isdigit():
        user_id = str(me["user_id"])  # the professional account's id, which publishing uses
    if me.get("account_type") and me["account_type"] not in ("BUSINESS", "MEDIA_CREATOR"):
        raise h.CommerceError("that Instagram account is a personal one: switch it to a Business or Creator account "
                              "in Instagram's settings, then connect again (Meta only lets those post through the API)")
    name = h.trim(me.get("username"), 60) or ""
    import vault
    vault.set_credential(TOKEN, token, actor=ACTOR, kind="token", service="Instagram",
                         label="Instagram connection (60 days, renewed automatically)", used_by=["instagram.publish"],
                         note=f"Account: @{name}" if name else "")
    vault.set_credential(USER, user_id, actor=ACTOR, kind="other", service="Instagram", label="Instagram user id")
    return {"connected": True, "username": name}


def _me(token: str) -> dict:
    try:
        return h.json_of("instagram", h.request("instagram", "GET", f"{API}/me", headers={}, secrets=(token,),
                                                params={"fields": "user_id,username,account_type",
                                                        "access_token": token})) or {}
    except h.CommerceError:
        return {}


def token() -> tuple[str, str]:
    """(access token, user id), renewing the token when it is over a week old."""
    import vault
    tok, uid = h.secret(TOKEN), h.secret(USER)
    if not tok or not uid:
        raise h.CommerceError("Instagram isn't connected: press Connect Instagram in Settings > Shops and social")
    row = next((c for c in vault.list_credentials() if c.get("name") == TOKEN), {})
    if time.time() - float(row.get("updated") or 0) > REFRESH_AFTER_S:
        try:
            new = h.json_of("instagram", h.request("instagram", "GET", "/refresh_access_token", headers={},
                                                   secrets=(tok,),
                                                   params={"grant_type": "ig_refresh_token", "access_token": tok}))
            if isinstance(new.get("access_token"), str) and new["access_token"]:
                tok = new["access_token"]
                vault.set_credential(TOKEN, tok, actor="instagram-token-refresh")
        except h.CommerceError:
            pass  # the current token still works until it expires; the next call tries again
    return tok, uid


def status() -> dict:
    import vault
    names = {"app_id": vault_name("social_instagram_app_id_name"),
             "app_secret": vault_name("social_instagram_app_secret_name")}
    have = {k: bool(h.secret(n)) for k, n in names.items()}
    row = next((c for c in vault.list_credentials() if c.get("name") == TOKEN), {})
    return {"names": names, "have": have, "app_ready": all(have.values()), "connected": bool(h.secret(TOKEN)),
            "account": str(row.get("note") or "").removeprefix("Account: ") or None,
            "enabled": h.enabled(SETTING), "scopes": SCOPES}
