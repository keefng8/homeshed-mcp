"""Sign in with OAuth: chat apps (Claude's custom connectors, ChatGPT, others) connect without anyone copying a token
(to-do #145's follow-on, the owner, 2026-10-27: "add real auth for easy setup on this connector").

The MCP SDK's own OAuth routes do the protocol (metadata, /authorize checks, PKCE, /token, /register, /revoke); this
module is the provider behind them, and the one HomeShed-specific step: the owner approves each sign-in on the
Control Panel. Nobody types a password into HomeShed, and a sign-in nobody approved never gets a token.

The flow:
1. The chat app finds us (/.well-known/oauth-protected-resource and /.well-known/oauth-authorization-server). It is
   either registered on the fly (DCR, /register) or names itself by a URL HomeShed reads (CIMD: Claude's "published
   identity", fetched once and checked: https only, public addresses only, the document's own client_id).
2. /authorize opens a page in the person's browser: "Approve this on your Control Panel", with a 6-digit code to
   match. The owner's phone hears about it, and the Control Panel lists it (GET /oauth/pending).
3. The owner approves it as a new app (or an existing one) with an access preset; the page then sends the browser
   back to the chat app with a one-time code (10 minutes, bound to its PKCE challenge).
4. /token gives an access token (hso_..., 1 hour) and a refresh token (60 days, a new one each refresh). Each maps to
   the app's name, so its grants, tool view, limits, suspension and deletion on the API page apply exactly as for a
   pasted token. Deleting the app ends its tokens.
Tokens are stored only as SHA-256 hashes (usage/oauth.json). OAuth is off until MCP_PUBLIC_URL is set (the https
address chat apps reach this server at); then the routes exist and a 401 on /mcp says where to sign in.

R&D's security review (27 Oct): a refresh token that comes back after it was rotated is treated as stolen and ends
the whole sign-in; the CIMD fetch checks the address it actually connected to (DNS rebinding) and runs off the event
loop; server.py rate-limits the sign-in routes per address and this module caps the owner's phone alerts. The store
is one JSON file under a threading lock: one server process (the Docker image runs one uvicorn worker).
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from paths import data_path

STORE_FILE = Path(os.environ.get("OAUTH_STORE_FILE") or data_path("usage/oauth.json"))
ACCESS_PREFIX = "hso_"
ACCESS_TTL_S = 3600
REFRESH_TTL_S = 60 * 86400
CODE_TTL_S = 600
PENDING_TTL_S = 600
MAX_REGISTRATIONS = 500
MAX_PENDING = 20
CIMD_MAX_BYTES = 64 * 1024
CIMD_CACHE_S = 3600
CIMD_CACHE_MAX = 200
ALERTS_PER_HOUR = 10
TRUST_TTL_S = 90 * 86400
MAX_TRUSTED = 50
PRESETS = ("none", "read", "all")
_lock = threading.Lock()
_cimd_cache: dict[str, tuple[float, dict]] = {}
_alerts: list[float] = []
_logger = logging.getLogger(__name__)


class OAuthError(ValueError):
    """An owner action that can't go ahead; the message says why."""


def public_url() -> str:
    """The https address chat apps use for this server, without a trailing slash, or "" (OAuth off)."""
    url = (os.environ.get("MCP_PUBLIC_URL") or "").strip().rstrip("/")
    return url if url.startswith("https://") or url.startswith("http://localhost") else ""


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- the store ---------------------------------------------------------------------------------------------------------

def _empty() -> dict:
    return {"clients": {}, "pending": {}, "codes": {}, "access": {}, "refresh": {}, "used_refresh": {}, "trusted": {}}


def _load() -> dict:
    try:
        data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError):
        _logger.error("the OAuth store %s is unreadable: no OAuth sign-in works until it's fixed", STORE_FILE)
        return {**_empty(), "unreadable": True}
    out = _empty()
    out.update({k: v for k, v in data.items() if k in out and isinstance(v, dict)})
    return out


def _save(data: dict) -> None:
    if data.get("unreadable"):
        raise OAuthError(f"the OAuth store ({STORE_FILE}) is unreadable; fix or remove it")
    now = time.time()
    data["pending"] = {k: v for k, v in data["pending"].items() if now - v["at"] < PENDING_TTL_S + 3600}
    data["codes"] = {k: v for k, v in data["codes"].items() if v["expires_at"] > now}
    data["access"] = {k: v for k, v in data["access"].items() if v["expires_at"] > now}
    data["refresh"] = {k: v for k, v in data["refresh"].items() if v["expires_at"] > now}
    data["used_refresh"] = {k: v for k, v in data["used_refresh"].items() if v["expires_at"] > now}
    data["trusted"] = {k: v for k, v in data["trusted"].items() if v["expires_at"] > now}
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, STORE_FILE)


# --- who a token belongs to (clients.resolve asks) ---------------------------------------------------------------------

def resolve(token: str) -> str | None:
    """The app name an OAuth access token belongs to, or None. The app's own state (suspended, expired, deleted) is
    then checked by clients.resolve, as for a pasted token."""
    if not token.startswith(ACCESS_PREFIX):
        return None
    row = _load()["access"].get(_h(token))
    if not row or row["expires_at"] <= time.time():
        return None
    return row["app"]


def end_app(app: str) -> int:
    """Revoke every OAuth token of one app (the API page deleted it or rotated its key). Returns how many went."""
    with _lock:
        data = _load()
        n = 0
        for kind in ("access", "refresh", "codes", "trusted"):
            for k in [k for k, v in data[kind].items() if v.get("app") == app]:
                del data[kind][k]
                n += 1
        if n:
            _save(data)
    return n


# --- CIMD: a client named by the URL of its own metadata document ------------------------------------------------------

def _redirect_ok(uri: str) -> bool:
    parts = urlsplit(uri)
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")  # a desktop app's callback


def fetch_cimd(client_id: str) -> dict | None:
    """The client metadata document at client_id (an https URL), checked; None when it isn't usable. Cached an hour."""
    import httpx
    import netguard
    hit = _cimd_cache.get(client_id)
    if hit and time.time() - hit[0] < CIMD_CACHE_S:
        return hit[1]
    parts = urlsplit(client_id)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or len(client_id) > 400:
        return None
    try:
        if ipaddress.ip_address(parts.hostname):  # a bare address is never a client's published identity
            return None
    except ValueError:
        pass
    try:
        netguard.resolve_permitted(parts.hostname)  # public addresses only: never a fetch into the owner's network
        with httpx.Client(timeout=5, follow_redirects=False) as http:
            with http.stream("GET", client_id, headers={"Accept": "application/json"}) as resp:
                stream = resp.extensions.get("network_stream")
                peer = stream.get_extra_info("server_addr") if stream is not None else None
                if not peer or not netguard.is_permitted(ipaddress.ip_address(peer[0])):
                    return None  # the name changed address after the check (DNS rebinding): never read it
                if resp.status_code != 200:
                    return None
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > CIMD_MAX_BYTES:
                        return None
        doc = json.loads(body)
    except Exception:  # noqa: BLE001 - any failure means "not a usable client", never a crash
        return None
    uris = doc.get("redirect_uris") if isinstance(doc, dict) else None
    if (doc.get("client_id") != client_id or not isinstance(uris, list) or not uris
            or not all(isinstance(u, str) and _redirect_ok(u) for u in uris)):
        return None
    clean = {"client_id": client_id, "redirect_uris": uris[:10],
             "client_name": str(doc.get("client_name") or parts.hostname)[:80],
             "client_uri": doc.get("client_uri") if isinstance(doc.get("client_uri"), str) else None}
    if len(_cimd_cache) >= CIMD_CACHE_MAX:
        _cimd_cache.pop(min(_cimd_cache, key=lambda k: _cimd_cache[k][0]))
    _cimd_cache[client_id] = (time.time(), clean)
    return clean


# --- the provider the SDK's routes call --------------------------------------------------------------------------------

class Provider:
    """mcp.server.auth.provider.OAuthAuthorizationServerProvider for HomeShed."""

    async def get_client(self, client_id: str):
        from mcp.shared.auth import OAuthClientInformationFull
        if client_id.startswith("https://"):
            import asyncio
            doc = await asyncio.to_thread(fetch_cimd, client_id)  # a slow site never holds up the other requests
            if doc is None:
                return None
            return OAuthClientInformationFull(
                client_id=client_id, redirect_uris=doc["redirect_uris"], client_name=doc["client_name"],
                token_endpoint_auth_method="none", grant_types=["authorization_code", "refresh_token"],
                response_types=["code"])
        row = _load()["clients"].get(client_id)
        return OAuthClientInformationFull.model_validate(row["info"]) if row else None

    async def register_client(self, client_info) -> None:
        from mcp.server.auth.provider import RegistrationError
        uris = [str(u) for u in client_info.redirect_uris or []]
        if not uris or not all(_redirect_ok(u) for u in uris):
            raise RegistrationError("invalid_redirect_uri", "redirect URIs must be https, or http on localhost")
        with _lock:
            data = _load()
            if len(data["clients"]) >= MAX_REGISTRATIONS:  # the oldest never-used registrations go first
                unused = sorted((k for k, v in data["clients"].items() if not v.get("used")),
                                key=lambda k: data["clients"][k]["at"])
                if not unused:
                    raise RegistrationError("invalid_client_metadata", "too many registered clients")
                del data["clients"][unused[0]]
            data["clients"][client_info.client_id] = {"info": client_info.model_dump(mode="json"), "at": time.time()}
            _save(data)

    async def authorize(self, client, params) -> str:
        from mcp.server.auth.provider import AuthorizeError
        base = public_url()
        if not base:
            raise AuthorizeError("temporarily_unavailable", "sign-in isn't set up on this server")
        if params.resource and params.resource.rstrip("/") not in (base, base + "/mcp"):
            raise AuthorizeError("invalid_target", "that resource isn't this server")
        with _lock:
            data = _load()
            waiting = [k for k, v in data["pending"].items() if v["status"] == "waiting"
                       and time.time() - v["at"] < PENDING_TTL_S]
            if len(waiting) >= MAX_PENDING:
                raise AuthorizeError("temporarily_unavailable", "too many sign-ins are waiting for the owner")
            pid = secrets.token_urlsafe(24)
            data["pending"][pid] = {
                "client_id": client.client_id, "client_name": (client.client_name or "an app")[:80],
                "redirect_uri": str(params.redirect_uri), "explicit": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge, "state": params.state, "scopes": params.scopes or [],
                "resource": params.resource, "at": time.time(), "status": "waiting",
                "match": f"{secrets.randbelow(1_000_000):06d}"}
            _save(data)
        return f"{base}/oauth/consent/{pid}"  # the owner is told when the page opens (consent_view)

    async def load_authorization_code(self, client, authorization_code: str):
        from mcp.server.auth.provider import AuthorizationCode
        row = _load()["codes"].get(_h(authorization_code))
        if not row or row["client_id"] != client.client_id or row["expires_at"] <= time.time():
            return None
        return AuthorizationCode(code=authorization_code, scopes=row["scopes"], expires_at=row["expires_at"],
                                 client_id=row["client_id"], code_challenge=row["code_challenge"],
                                 redirect_uri=row["redirect_uri"], redirect_uri_provided_explicitly=row["explicit"],
                                 resource=row.get("resource"), subject=row["app"])

    async def exchange_authorization_code(self, client, authorization_code):
        from mcp.server.auth.provider import TokenError
        with _lock:
            data = _load()
            row = data["codes"].pop(_h(authorization_code.code), None)  # one use only
            if not row:
                raise TokenError("invalid_grant", "that code was already used or has expired")
            token = self._issue(data, row["app"], client.client_id, row["scopes"], row.get("resource"))
            if client.client_id in data["clients"]:
                data["clients"][client.client_id]["used"] = time.time()
            _save(data)
        return token

    async def load_refresh_token(self, client, refresh_token: str):
        from mcp.server.auth.provider import RefreshToken
        key = _h(refresh_token)
        used = _load()["used_refresh"].get(key)
        if used:  # a rotated refresh token came back: someone else has a copy. End the whole sign-in.
            with _lock:
                data = _load()
                _end_grant(data, used["grant"])
                _save(data)
            _logger.warning("an old refresh token of %s came back: its sign-in was ended", used.get("app"))
            return None
        row = _load()["refresh"].get(key)
        if not row or row["client_id"] != client.client_id or row["expires_at"] <= time.time():
            return None
        import clients
        if row["app"] not in clients._load()["clients"]:  # the app was deleted: its sign-in ended with it
            return None
        return RefreshToken(token=refresh_token, client_id=row["client_id"], scopes=row["scopes"],
                            expires_at=int(row["expires_at"]), resource=row.get("resource"), subject=row["app"])

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        from mcp.server.auth.provider import TokenError
        with _lock:
            data = _load()
            key = _h(refresh_token.token)
            row = data["refresh"].pop(key, None)  # rotated: the old one stops working
            if not row:
                raise TokenError("invalid_grant", "that refresh token was already used or has expired")
            data["used_refresh"][key] = {"grant": row["grant"], "app": row["app"], "expires_at": row["expires_at"]}
            token = self._issue(data, row["app"], client.client_id, row["scopes"], row.get("resource"),
                                grant=row["grant"])
            _save(data)
        return token

    async def load_access_token(self, token: str):
        from mcp.server.auth.provider import AccessToken
        row = _load()["access"].get(_h(token))
        if not row or row["expires_at"] <= time.time():
            return None
        return AccessToken(token=token, client_id=row["client_id"], scopes=row["scopes"],
                           expires_at=int(row["expires_at"]), resource=row.get("resource"), subject=row["app"])

    async def revoke_token(self, token) -> None:
        with _lock:
            data = _load()
            key = _h(token.token)
            row = data["access"].pop(key, None) or data["refresh"].pop(key, None)
            if row:  # a revoke ends the whole sign-in: its other tokens too
                _end_grant(data, row.get("grant"))
                _save(data)

    async def exchange_identity_assertion(self, *args, **kwargs):
        from mcp.server.auth.provider import TokenError
        raise TokenError("unsupported_grant_type", "identity assertions aren't supported")

    @staticmethod
    def _issue(data: dict, app: str, client_id: str, scopes: list[str], resource: str | None, grant: str = ""):
        """New tokens for one sign-in. grant ties every token of one sign-in together (kept through refreshes), so a
        revoke or a reused refresh token ends all of them."""
        from mcp.shared.auth import OAuthToken
        now, grant = time.time(), grant or secrets.token_hex(8)
        access = ACCESS_PREFIX + secrets.token_urlsafe(32)
        refresh = "hsr_" + secrets.token_urlsafe(40)
        base = {"app": app, "client_id": client_id, "scopes": scopes, "resource": resource, "grant": grant}
        data["access"][_h(access)] = {**base, "expires_at": now + ACCESS_TTL_S}
        data["refresh"][_h(refresh)] = {**base, "expires_at": now + REFRESH_TTL_S}
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL_S, refresh_token=refresh,
                          scope=" ".join(scopes) or None)


def _end_grant(data: dict, grant: str | None) -> None:
    if not grant:
        return
    for kind in ("access", "refresh"):
        for k in [k for k, v in data[kind].items() if v.get("grant") == grant]:
            del data[kind][k]


def _tell_owner(item: dict) -> None:
    now = time.time()
    _alerts[:] = [t for t in _alerts if now - t < 3600]
    if len(_alerts) >= ALERTS_PER_HOUR:  # a flood of sign-ins never floods the owner's phone: the panel lists them
        return
    _alerts.append(now)
    try:
        import printify_pending
        printify_pending._note(f"{item['client_name']} wants to connect to HomeShed. Approve or decline it on the "
                               f"Control Panel (code {item['match']}).", "New sign-in to HomeShed", "high", "key")
    except Exception:  # noqa: BLE001 - the Control Panel lists it either way
        pass


# --- the owner's side (Control Panel) ----------------------------------------------------------------------------------

def pending() -> list[dict]:
    """Sign-ins waiting for the owner, newest first, with what the owner needs to decide."""
    now, out = time.time(), []
    for pid, v in _load()["pending"].items():
        if v["status"] == "waiting" and now - v["at"] < PENDING_TTL_S:
            out.append({"id": pid, "app_name": v["client_name"], "client_id": v["client_id"][:200],
                        "client_host": urlsplit(v["client_id"]).hostname if v["client_id"].startswith("https://")
                        else None, "verified": v["client_id"].startswith("https://"),
                        "redirect_host": urlsplit(v["redirect_uri"]).hostname, "match": v["match"], "at": v["at"],
                        "expires_in_s": int(PENDING_TTL_S - (now - v["at"])), "suggested_name": suggest_name(v)})
    return sorted(out, key=lambda r: -r["at"])


def suggest_name(item: dict) -> str:
    import clients
    stem = re.sub(r"[^a-z0-9]+", "-", item["client_name"].lower()).strip("-")[:24] or "app"
    if not stem[0].isalpha():
        stem = "app-" + stem
    taken, name, n = clients._load()["clients"], stem, 2
    while name in taken or name == "owner" or not clients.NAME_RE.match(name):
        name, n = f"{stem[:28]}-{n}", n + 1
    return name


def decide(pid: str, approve: bool, app: str = "", preset: str = "read", trust: bool = False) -> dict:
    """The owner's answer to one sign-in. Approve as a new app (named app, or the suggested name) with an access
    preset, or as an existing app (its grants are kept). trust: the browser that asked gets a 90-day cookie, and
    the same app signing in again from that browser to the same return address is approved without asking.
    Returns {"id", "status", "app"?, "trusted"?}."""
    import clients
    from mcp.server.auth.provider import construct_redirect_uri
    with _lock:
        data = _load()
        item = data["pending"].get(pid)
        if not item or item["status"] != "waiting" or time.time() - item["at"] >= PENDING_TTL_S:
            raise OAuthError("that sign-in isn't waiting any more (they last 10 minutes): ask for a new one")
        if not approve:
            item["status"] = "declined"
            item["redirect"] = construct_redirect_uri(item["redirect_uri"], error="access_denied",
                                                      error_description="the owner declined", state=item["state"])
            _save(data)
            return {"id": pid, "status": "declined"}
        if preset not in PRESETS:
            raise OAuthError(f"preset must be one of {list(PRESETS)}")
        name = (app or "").strip() or suggest_name(item)
        if name not in clients._load()["clients"]:
            clients.create(name, note=f"Signed in with OAuth: {item['client_name']}", preset=preset)  # its pasted
            # token is never shown: this app connects with OAuth tokens only
        code = secrets.token_urlsafe(32)
        data["codes"][_h(code)] = {"app": name, "client_id": item["client_id"], "scopes": item["scopes"],
                                   "code_challenge": item["code_challenge"], "redirect_uri": item["redirect_uri"],
                                   "explicit": item["explicit"], "resource": item["resource"],
                                   "expires_at": time.time() + CODE_TTL_S}
        item.update(status="approved", app=name,
                    redirect=construct_redirect_uri(item["redirect_uri"], code=code, state=item["state"]))
        if trust:
            if len(data["trusted"]) >= MAX_TRUSTED:
                raise OAuthError(f"{MAX_TRUSTED} trusted apps already: remove one on the API page first")
            cookie = secrets.token_urlsafe(32)
            data["trusted"][_h(cookie)] = {"app": name, "client_id": item["client_id"],
                                           "client_name": item["client_name"], "redirect_uri": item["redirect_uri"],
                                           "at": time.time(), "expires_at": time.time() + TRUST_TTL_S}
            item["trust_cookie"] = cookie  # handed to the asking browser once, with its redirect
        _save(data)
    clients._audit("oauth sign-in approved", name, oauth_client=item["client_id"][:120], trusted=bool(trust))
    return {"id": pid, "status": "approved", "app": name, **({"trusted": True} if trust else {})}


def trust_cookie_name(client_id: str) -> str:
    """One cookie per app, so a browser can trust several."""
    return "hst_" + _h(client_id)[:16]


def consent_view(pid: str, cookie: str | None) -> None:
    """The consent page opened. A browser the owner trusted for this same app and return address is approved at
    once, as the app it was approved as; otherwise the owner is told about the sign-in. Trust is tied to the browser,
    never to the app's id alone: anyone can start a sign-in as a public app like claude.ai from their own account."""
    import clients
    data = _load()
    item = data["pending"].get(pid)
    if not item or item["status"] != "waiting" or item.get("told"):
        return
    row = data["trusted"].get(_h(cookie)) if cookie else None
    if (row and row["expires_at"] > time.time() and row["client_id"] == item["client_id"]
            and row["redirect_uri"] == item["redirect_uri"]):
        app_row = clients._load()["clients"].get(row["app"])
        if app_row and not app_row.get("suspended"):
            try:
                decide(pid, True, row["app"])
                clients._audit("oauth sign-in approved by a trusted browser", row["app"],
                               oauth_client=item["client_id"][:120])
                return
            except OAuthError:
                pass
    with _lock:
        data = _load()
        if pid in data["pending"]:
            data["pending"][pid]["told"] = True
            _save(data)
    _tell_owner(item)


def trusted() -> list[dict]:
    """Trusted browsers, for the API page: never the cookie, only an id to remove one by."""
    now = time.time()
    return sorted(({"id": k[:16], "app": v["app"], "app_name": v["client_name"],
                    "redirect_host": urlsplit(v["redirect_uri"]).hostname, "at": v["at"],
                    "expires_in_days": int((v["expires_at"] - now) // 86400)}
                   for k, v in _load()["trusted"].items() if v["expires_at"] > now), key=lambda r: -r["at"])


def untrust(tid: str) -> bool:
    """Forget one trusted browser; its next sign-in asks the owner again. Sign-ins already made are kept."""
    with _lock:
        data = _load()
        keys = [k for k in data["trusted"] if k[:16] == tid] if len(tid) == 16 else []
        for k in keys:
            del data["trusted"][k]
        if keys:
            _save(data)
    return bool(keys)


def consent_status(pid: str) -> dict:
    """What the waiting browser page shows: waiting, or where to go now (once). Unknown ids look expired."""
    with _lock:
        data = _load()
        item = data["pending"].get(pid)
        if not item:
            return {"status": "expired"}
        if item["status"] == "waiting" and time.time() - item["at"] >= PENDING_TTL_S:
            return {"status": "expired"}
        out = {"status": item["status"], "app_name": item["client_name"], "match": item["match"]}
        if item.get("redirect"):
            out["redirect"] = item.pop("redirect")  # handed out once
            if item.get("trust_cookie"):  # the route turns this into a cookie; never in the page's JSON
                out["_trust_cookie"] = (trust_cookie_name(item["client_id"]), item.pop("trust_cookie"))
            item["status"] = "done-" + item["status"]
            _save(data)
        return out


CONSENT_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Connect to HomeShed</title><style>
:root{color-scheme:light dark;--fg:#1d1d1f;--mute:#6e6e73;--bg:#f5f5f7;--card:#fff;--acc:#c2410c}
@media (prefers-color-scheme:dark){:root{--fg:#f5f5f7;--mute:#a1a1a6;--bg:#000;--card:#1c1c1e;--acc:#fb923c}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}.c{background:var(--card);border-radius:16px;
padding:28px 24px;max-width:420px;margin:16px;box-shadow:0 8px 30px rgba(0,0,0,.12)}h1{font-size:20px;margin:0 0 8px}
p{margin:8px 0;color:var(--mute)}.code{font:600 28px/1 ui-monospace,monospace;letter-spacing:.15em;color:var(--acc);
margin:16px 0}#s{color:var(--fg);font-weight:600}</style></head><body><div class="c">
<h1>Connect <span id="n">this app</span> to HomeShed</h1>
<p>Open your HomeShed Control Panel and approve this sign-in. Check it shows the same code:</p>
<div class="code" id="m">------</div><p id="s">Waiting for your approval…</p>
<p>You can leave this page open; it moves on by itself. It expires in 10 minutes.</p></div>
<script>(function(){var u=location.pathname.replace(/\\/$/,"")+"/status";
function tick(){fetch(u,{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
if(d.app_name)document.getElementById("n").textContent=d.app_name;if(d.match)document.getElementById("m").textContent=d.match;
if(d.redirect){document.getElementById("s").textContent=d.status==="approved"?"Approved: taking you back…":"Declined.";
location.replace(d.redirect);return;}
if(d.status==="expired"||d.status.indexOf("done-")===0){document.getElementById("s").textContent=
d.status==="expired"?"This sign-in expired. Start again from the app.":"Finished: you can close this page.";return;}
setTimeout(tick,2000);}).catch(function(){setTimeout(tick,4000);});}tick();})();</script></body></html>"""
