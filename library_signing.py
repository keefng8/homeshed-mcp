"""HomeShed signs read-only library scripts from its vault (Homeshed-addon issue #58, publisher trust).

The same message and POST as the add-on's `homeshed-library publisher sign`: the Ed25519 key is LIBRARY_PUBLISHER_KEY in
the vault (its 32 bytes in base64url, the line `publisher init` writes). It's off until the owner makes that key and
stores it there; no agent ever needs it. Rules:
- An agent only asks (publisher.sign): the script's body is fetched, its SHA-256 worked out here, and the request waits
  in the Control Panel's approvals bar with the code to read. Only the owner's Yes there signs.
- On Yes the body is fetched again; if it changed since the request, nothing is signed.
- Read-only scripts only, as the library says (risk read). The runner still scans the body itself before it trusts a
  signature, so a wrong label can't make it run something that writes.
- The library never signs; it checks the signature against the public key the owner added there.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from paths import data_path

STORE_FILE = Path(os.environ.get("LIBRARY_SIGN_FILE") or data_path("usage/library_sign_requests.json"))
KEY_NAME, LIBRARY_KEY_NAME = "LIBRARY_PUBLISHER_KEY", "HOMESHED_LIBRARY_OWNER"
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}")
MAX_WAITING, MAX_BODY, TIMEOUT_S = 20, 200_000, 30
_lock = threading.Lock()


class SigningError(RuntimeError):
    """A request or signature that can't be made. The message is safe to show the caller."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def body_sha256(body: str) -> str:
    """As the library and runner hash a version: exactly what /raw serves, the body and a final newline."""
    return hashlib.sha256((body + "\n").encode("utf-8")).hexdigest()


def message(asset_id: str, sha256: str) -> bytes:
    """What a publisher signs: this exact content of this script, as read-only (the risk is part of it)."""
    return f"homeshed-publisher-v1\n{asset_id}\n{sha256}\nread".encode()


def fingerprint(public_raw: bytes) -> str:
    h = hashlib.sha256(b"homeshed-publisher:" + public_raw).hexdigest()[:16]
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def _private_key():
    import vault
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    text = (vault.secret(KEY_NAME) or "").strip()
    if not text:
        raise SigningError(f"signing is off: there's no {KEY_NAME} in the vault. The owner makes one when they choose "
                           "(homeshed-library publisher init on their PC, then scripts/vault-put.py "
                           f"{KEY_NAME} --from-file <that key file>)")
    try:
        seed = unb64url(text)
    except ValueError:
        seed = b""
    if len(seed) != 32:
        raise SigningError(f"{KEY_NAME} in the vault isn't 32 bytes in base64url")
    return Ed25519PrivateKey.from_private_bytes(seed)


def _public_fp(private) -> str:
    from cryptography.hazmat.primitives import serialization
    return fingerprint(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def _library() -> tuple[str, str]:
    import runtime_settings
    import vault
    url = str(runtime_settings.get("library_url") or "").strip().rstrip("/")
    key = (vault.secret(LIBRARY_KEY_NAME) or "") if url else ""
    if not url or not key:
        raise SigningError(f"the library isn't linked: set its address in Settings and {LIBRARY_KEY_NAME} in the vault")
    return url, key


def _call(path: str, body: dict | None = None) -> dict:
    import httpx
    url, key = _library()
    try:
        r = httpx.request("POST" if body is not None else "GET", url + path, json=body, timeout=TIMEOUT_S,
                          headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise SigningError(f"the library can't be reached: {type(exc).__name__}") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or not isinstance(data, dict):
        said = data.get("error") if isinstance(data, dict) else ""
        raise SigningError(str(said or f"the library answered {r.status_code}")[:300])
    return data


def _script(asset_id: str) -> dict:
    """The script's current version, checked: its hash worked out here, risk read. Adds `sha` (ours)."""
    script = _call(f"/publisher/scripts/{asset_id}")
    body = str(script.get("body") or "")
    if len(body) > MAX_BODY:
        raise SigningError(f"{asset_id} is over {MAX_BODY} characters: sign it with the publisher command instead")
    sha = body_sha256(body)
    if sha != script.get("sha256"):
        raise SigningError("the library's SHA-256 for that body isn't the one worked out here: nothing signed")
    if script.get("risk") != "read":
        raise SigningError(f"{asset_id} is risk {script.get('risk')}: only read-only scripts can be signed for now")
    return {**script, "body": body, "sha": sha}


def _load() -> dict:
    try:
        data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise SigningError(f"{STORE_FILE.name} is unreadable; the owner must fix or remove it") from None
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(rows: dict) -> None:
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STORE_FILE)


def _id(asset_id: str) -> str:
    asset_id = str(asset_id or "").strip().lower()
    if not ID_RE.fullmatch(asset_id):
        raise SigningError("id: a library script id, e.g. ops.disk-report")
    return asset_id


def request(asset_id: str, by: str, now: float | None = None) -> dict:
    """An agent asks for a script's current version to be signed; it waits for the owner's Yes."""
    asset_id = _id(asset_id)
    fp = _public_fp(_private_key())  # off without a key: nothing queues
    script = _script(asset_id)
    if fp in (script.get("signed_by") or []):
        return {"id": asset_id, "version": script.get("version"), "status": f"signed by {fp} already"}
    with _lock:
        rows = _load()
        if asset_id not in rows and len(rows) >= MAX_WAITING:
            raise SigningError(f"{MAX_WAITING} requests are waiting already; the owner decides those first")
        rows[asset_id] = {"id": asset_id, "version": script.get("version"), "sha256": script["sha"],
                          "title": str(script.get("title") or "")[:200], "language": str(script.get("language") or ""),
                          "runs_on": str(script.get("runs_on") or ""), "body": script["body"],
                          "by": str(by or "")[:60], "at": time.time() if now is None else now}
        _save(rows)
    return {"id": asset_id, "version": script.get("version"), "publisher": fp,
            "status": "waiting for the owner: they read the code and say Yes in the Control Panel's approvals bar"}


def waiting() -> list[dict]:
    return sorted(_load().values(), key=lambda r: -float(r.get("at") or 0))


def decide(asset_id: str, action: str) -> dict:
    """The owner (Control Panel only): approve signs the content they read, if it's still the current one; decline
    drops the request."""
    if action not in ("approve", "decline"):
        raise SigningError("action must be approve or decline")
    asset_id = _id(asset_id)
    with _lock:
        rows = _load()
        row = rows.pop(asset_id, None)
        if row is None:
            raise SigningError("no such request")
        if action == "decline":
            _save(rows)
            return {"id": asset_id, "action": "decline"}
        private = _private_key()
        script = _script(asset_id)
        if script["sha"] != row.get("sha256"):
            _save(rows)
            raise SigningError(f"{asset_id} changed since it was asked for (now v{script.get('version')}): nothing "
                               "signed; ask again to read the new version")
        fp = _public_fp(private)
        out = _call("/publisher/signatures", {"id": asset_id, "sha256": script["sha"], "publisher": fp,
                                              "sig": b64url(private.sign(message(asset_id, script["sha"])))})
        _save(rows)
    return {"id": asset_id, "action": "approve", "version": out.get("version"), "publisher": fp}
