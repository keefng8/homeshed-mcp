"""Pinterest (API v5) for pinterest.* and the Control Panel's Connect Pinterest card. The owner's decision, 2026-10-10
(Director's decisions item 5): an approval-gated posting tool, built up to the point where the business account can be
connected. R&D's scope: researchandimprovements/pinterest-scope-review.md.

Pinterest's spec (github.com/pinterest/api-description, v5 5.28.0, read 2026-10-10) and docs:
- Sign-in: www.pinterest.com/oauth/ (response_type=code, comma-separated scopes, state). The code is swapped at
  /v5/oauth/token with HTTP Basic auth (app id : secret), form grant_type=authorization_code, code, redirect_uri;
  refreshed with grant_type=refresh_token (an app made before 25 Sep 2025 sends continuous_refresh=true to get a new
  refresh token each time).
- Trial access (every new app): pins are made in the SANDBOX, api-sandbox.pinterest.com, visible only to the account
  owner. Standard access (after Pinterest's app review) makes them public at api.pinterest.com. A sandbox token doesn't
  work in production and the other way round, so each environment keeps its own tokens and needs its own Connect.

Kept in the vault: PINTEREST[_SANDBOX]_ACCESS_TOKEN and PINTEREST[_SANDBOX]_REFRESH_TOKEN. The app id and secret are
read by the names set in Settings > Shops and social. Nothing here returns or logs a token or the secret.
"""
from __future__ import annotations

import base64
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from tools.commerce import _http as h
from tools.commerce import vault_name

AUTHORIZE_URL = "https://www.pinterest.com/oauth/"
SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"
SETTING = "social_pinterest_enabled"
SANDBOX_SETTING = "social_pinterest_sandbox"
ACTOR = "pinterest-connect"
PENDING_S, MAX_PENDING = 600, 5
REFRESH_AFTER_S = 20 * 86400  # access tokens last 30 days
_lock = threading.Lock()
_pending: dict[str, dict] = {}


def sandbox() -> bool:
    """Trial access: everything goes to Pinterest's sandbox. On unless the owner switches it off (after Standard)."""
    try:
        import runtime_settings
        return runtime_settings.get(SANDBOX_SETTING) is not False
    except Exception:  # noqa: BLE001 - no settings: the safe side
        return True


def service(sb: bool | None = None) -> str:
    return "pinterest-sandbox" if (sandbox() if sb is None else sb) else "pinterest"


def names(sb: bool | None = None) -> tuple[str, str]:
    pre = "PINTEREST_SANDBOX_" if (sandbox() if sb is None else sb) else "PINTEREST_"
    return pre + "ACCESS_TOKEN", pre + "REFRESH_TOKEN"


def _app() -> tuple[str, str]:
    iname, sname = vault_name("social_pinterest_app_id_name"), vault_name("social_pinterest_app_secret_name")
    app_id, secret = h.secret(iname), h.secret(sname)
    missing = [n for n, v in ((iname, app_id), (sname, secret)) if not v]
    if missing:
        raise h.missing(missing, "Pinterest")
    return app_id, secret


def _basic(app_id: str, secret: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{app_id}:{secret}".encode()).decode(),
            "Accept": "application/json"}


def _check_redirect(uri: str) -> str:
    uri = (uri or "").strip()
    parts = urlsplit(uri)
    if parts.scheme != "https" or not parts.hostname or parts.fragment or parts.username or parts.password or len(uri) > 300:
        raise h.CommerceError("the return address must be a full https:// address (Pinterest refuses anything else)")
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
        _pending[state] = {"redirect_uri": redirect_uri, "expires": now + PENDING_S, "sandbox": sandbox()}
    url = AUTHORIZE_URL + "?" + urlencode({"client_id": app_id, "redirect_uri": redirect_uri, "scope": SCOPES,
                                           "response_type": "code", "state": state})
    return {"url": url, "redirect_uri": redirect_uri, "expires_in_s": PENDING_S, "sandbox": sandbox()}


def finish_url(pasted: str) -> dict:
    q = parse_qs(urlsplit((pasted or "").strip().split("#", 1)[0]).query)
    if q.get("error"):
        raise h.CommerceError(f"Pinterest said: {h.clean(q.get('error_description', q['error'])[0])}")
    code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
    if not code or not state:
        raise h.CommerceError("that address has no code from Pinterest: paste the whole address it sent you to")
    return finish(code, state)


def _token_call(form: dict, sb: bool, extra_secrets: tuple = ()) -> dict:
    app_id, secret = _app()
    body = h.json_of("pinterest", h.request(service(sb), "POST", "/v5/oauth/token", headers=_basic(app_id, secret),
                                            secrets=(secret, *extra_secrets), data=form))
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str) or not body["access_token"]:
        raise h.CommerceError("Pinterest's answer had no token")
    return body


def finish(code: str, state: str) -> dict:
    with _lock:
        pend = _pending.pop(state or "", None)
    if not pend:
        raise h.CommerceError("this sign-in isn't one the panel started, or it was already used: press Connect again")
    if pend["expires"] < time.time():
        raise h.CommerceError("the sign-in took longer than 10 minutes: press Connect again")
    sb = bool(pend["sandbox"])
    body = _token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": pend["redirect_uri"],
                        "continuous_refresh": "true"}, sb, (code,))
    _save(body, sb)
    name = _username(body["access_token"], sb)
    return {"connected": True, "username": name, "sandbox": sb}


def _save(body: dict, sb: bool, actor: str = ACTOR) -> None:
    import vault
    access, refresh = names(sb)
    env = "sandbox (Trial access)" if sb else "public (Standard access)"
    vault.set_credential(access, body["access_token"], actor=actor, kind="token", service="Pinterest",
                         label=f"Pinterest connection, {env} (30 days, renewed automatically)",
                         used_by=["pinterest.* tools"])
    if isinstance(body.get("refresh_token"), str) and body["refresh_token"]:
        vault.set_credential(refresh, body["refresh_token"], actor=actor, kind="token", service="Pinterest",
                             label=f"Pinterest renewal token, {env}")


def _username(tok: str, sb: bool) -> str:
    try:
        me = h.json_of("pinterest", h.request(service(sb), "GET", "/v5/user_account", secrets=(tok,),
                                              headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"}))
        return h.trim(me.get("username"), 60) or ""
    except h.CommerceError:
        return ""


def token() -> str:
    """The access token for the current environment, renewed when it's over 20 days old."""
    import vault
    access, refresh = names()
    tok = h.secret(access)
    if not tok:
        raise h.CommerceError("Pinterest isn't connected" + (" (sandbox)" if sandbox() else "")
                              + ": press Connect Pinterest in Settings > Shops and social")
    row = next((c for c in vault.list_credentials() if c.get("name") == access), {})
    ref = h.secret(refresh)
    if ref and time.time() - float(row.get("updated") or 0) > REFRESH_AFTER_S:
        try:
            _save(_token_call({"grant_type": "refresh_token", "refresh_token": ref, "continuous_refresh": "true"},
                              sandbox(), (ref, tok)), sandbox(), actor="pinterest-token-refresh")
            tok = h.secret(access) or tok
        except h.CommerceError:
            pass  # the current token still works until it expires; the next call tries again
    return tok


def call(method: str, path: str, json: dict | None = None, params: dict | None = None) -> dict:
    """One API call in the current environment (sandbox or public)."""
    tok = token()
    return h.json_of("pinterest", h.request(service(), method, path, json=json, params=params, secrets=(tok,),
                                            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"}))


def status() -> dict:
    import vault
    nm = {"app_id": vault_name("social_pinterest_app_id_name"), "app_secret": vault_name("social_pinterest_app_secret_name")}
    have = {k: bool(h.secret(n)) for k, n in nm.items()}
    access, _ = names()
    return {"names": nm, "have": have, "app_ready": all(have.values()), "connected": bool(h.secret(access)),
            "enabled": h.enabled(SETTING), "sandbox": sandbox(), "scopes": SCOPES,
            "connected_other_env": bool(h.secret(names(not sandbox())[0]))}
