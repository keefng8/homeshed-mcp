"""Credentials vault: every credential in one place the owner controls, so every AI session uses the same ones
without ever seeing them.

Every Claude session uses the same credentials *through the tools*: a tool asks for one by name (secret("NAME")),
and no MCP tool ever returns a value, because anything a Claude session reads is saved in plain text in its
transcript. The owner manages them through the admin routes: add, replace, delete, generate a new random value, and reveal. Revealing needs the reveal
key, which only the dashboard holds (after it re-checks the owner's password), so even a session holding the owner
token can't read a value back through this server.

Storage: VAULT_FILE (default /data/vault/vault.json, its own named volume). Each value is encrypted with Fernet
(AES-128-CBC with HMAC-SHA256, from the `cryptography` package) under VAULT_KEY from .env. Written atomically with
file mode 600; every change goes to an audit trail that never holds a value. With no VAULT_KEY (or a wrong one),
lookups fall back to the environment and changes are refused, so the server works exactly as before the vault.

The default install (`uvx homeshed-mcp` started by an AI app, over stdio) has no .env and so no VAULT_KEY. There the
key comes from KEY_FILE in the data folder (vault/vault.key, mode 600), which only an explicit `homeshed-mcp pro
connect` creates (ensure_local_key; 2026-10-01: without it a pasted Pro key had nowhere to go). VAULT_KEY in the
environment always wins, so Docker and HTTP installs are unchanged.

Limits, stated plainly: this stops *accidental* exposure (a tool result, a log, a transcript). It is not a wall
against someone with root on the host, who can read VAULT_KEY from the same .env, or the key file from the same disk.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import secrets as _random
import threading
import time
from pathlib import Path
from paths import data_path

VAULT_FILE = Path(os.environ.get("VAULT_FILE") or data_path("vault/vault.json"))
AUDIT_FILE = Path(os.environ.get("VAULT_AUDIT_FILE") or data_path("vault/audit.jsonl"))
KEY_FILE = Path(os.environ.get("VAULT_KEY_FILE") or data_path("vault/vault.key"))
NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
KINDS = ("api-key", "token", "password", "ssh-key", "login", "other")
TEXT_FIELDS = {"label": 80, "service": 40, "username": 120, "host": 255, "note": 400}
MAX_VALUE = 16_384          # an SSH private key fits; nothing else needs more
_logger = logging.getLogger("vault")
_cache: tuple = (None, None)
_lock = threading.RLock()   # one change at a time: read, modify and write never interleave


class VaultError(ValueError):
    """A change the vault refuses (bad name, no key, missing entry). Safe to show: it never holds a value."""


def _key() -> str:
    """VAULT_KEY from the environment, else this machine's key file (see the module docstring), else "". The file is
    refused if others may read it (POSIX), and on Windows it's sealed to this user with DPAPI (R&D's review)."""
    key = os.environ.get("VAULT_KEY", "").strip()
    if key:
        return key
    try:
        if os.name != "nt" and KEY_FILE.stat().st_mode & 0o077:
            _logger.error("%s can be read by other users: the vault stays off until it's chmod 600", KEY_FILE)
            return ""
        text = KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.startswith("dpapi:"):
        try:
            import base64
            return _dpapi(base64.b64decode(text[6:]), protect=False).decode("ascii")
        except (OSError, ValueError):
            _logger.error("%s can't be unsealed by this Windows user: the vault stays off", KEY_FILE)
            return ""
    return text


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows' Data Protection API: seal (or unseal) bytes so only this Windows user on this machine can read them."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.WinDLL("crypt32", use_last_error=True), ctypes.WinDLL("kernel32")
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not call(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):  # 0x1: no UI
        raise OSError(f"DPAPI failed (error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _fernet():
    """The cipher, or None when there's no key or it's malformed (the vault is then off)."""
    key = _key()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except (ImportError, ValueError):
        _logger.error("The vault key is set but unusable: the vault is off until it's fixed")
        return None


def enabled() -> bool:
    return _fernet() is not None


def ensure_local_key() -> bool:
    """On an install with no VAULT_KEY, create this machine's key file (mode 600) if there isn't one yet, so the vault
    can hold a pasted Pro key. Never replaces a key. True when the vault works afterwards."""
    if os.environ.get("VAULT_KEY", "").strip() or KEY_FILE.exists():
        return enabled()
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    if os.name == "nt":  # mode 600 means nothing on Windows: seal it to this user instead
        import base64
        text = "dpapi:" + base64.b64encode(_dpapi(key, protect=True)).decode("ascii")
    else:
        text = key.decode("ascii")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # O_EXCL: never over another key
    except FileExistsError:
        return enabled()
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return enabled()


def forget_local_key_if_unused() -> bool:
    """After `pro disconnect`: delete this machine's key file when the vault no longer holds anything (R&D's review).
    Never with VAULT_KEY in the environment, and never while another credential still needs it. True if deleted."""
    if os.environ.get("VAULT_KEY", "").strip() or not KEY_FILE.exists():
        return False
    with _lock:
        if _load()["credentials"]:
            return False
        KEY_FILE.unlink()
    return True


def _load() -> dict:
    global _cache
    try:
        st = VAULT_FILE.stat()
    except FileNotFoundError:
        return {"credentials": {}}
    except OSError:
        return {"credentials": {}, "unreadable": True}
    sig = (str(VAULT_FILE), st.st_mtime_ns, st.st_size)
    if sig == _cache[0]:
        return _cache[1]
    try:
        data = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("credentials"), dict):
            raise ValueError("no credentials object")
    except (OSError, ValueError):
        _logger.error("vault file %s is unreadable: using the environment only", VAULT_FILE)
        return {"credentials": {}, "unreadable": True}
    _cache = (sig, data)
    return data


def _save(data: dict) -> None:
    global _cache
    VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VAULT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, VAULT_FILE)
    # Cache what was just written: a new value of the same length keeps the file's size, and inside one clock tick its
    # mtime too, so the (mtime, size) check alone could keep serving the old secret (clients.py had this, 2026-09-29).
    st = VAULT_FILE.stat()
    _cache = ((str(VAULT_FILE), st.st_mtime_ns, st.st_size), json.loads(json.dumps(data)))


def _audit(action: str, name: str, actor: str, **detail) -> None:
    """Who changed or revealed what, and when. Never a value."""
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "action": action, "name": name, "by": actor, **detail}) + "\n")
    except OSError:
        _logger.error("could not write the vault audit trail at %s", AUDIT_FILE)


def note_wrong_reveal_key(name: str) -> None:
    """A reveal (or a test to another address) sent with a wrong reveal key: server.py counts them and stops after
    five in ten minutes; each one goes in the audit trail. The name is shortened, since a caller chose it."""
    _audit("wrong reveal key", str(name)[:64], "unknown")


def _editable():
    f = _fernet()
    if f is None:
        raise VaultError("the vault is off: VAULT_KEY isn't set on the tool server")
    data = _load()
    if data.get("unreadable"):
        raise VaultError("the vault file is unreadable; fix or restore it before changing credentials")
    return f, copy.deepcopy(data)


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME.match(name):
        raise VaultError("a name is CAPITALS, digits and underscores, starting with a letter (like NVIDIA_API_KEY)")
    return name


def _public(name: str, entry: dict) -> dict:
    out = {k: v for k, v in entry.items() if k != "value"}
    out["name"] = name
    return out


def list_credentials() -> list[dict]:
    """Every credential's details, never its value."""
    return [_public(n, e) for n, e in sorted(_load()["credentials"].items())]


def set_credential(name: str, value: str | None = None, actor: str = "owner", **meta) -> dict:
    with _lock:
        return _set(name, value, actor, **meta)


def _set(name: str, value: str | None, actor: str, **meta) -> dict:
    """Add or replace a credential, or (value=None) change only its details. Returns its details."""
    f, data = _editable()
    _check_name(name)
    entry = data["credentials"].get(name)
    if entry is None and value is None:
        raise VaultError(f"{name} isn't in the vault yet: give it a value")
    entry = dict(entry or {"created": time.time()})
    kind = meta.get("kind", entry.get("kind", "token"))
    if kind not in KINDS:
        raise VaultError(f"kind must be one of: {', '.join(KINDS)}")
    entry["kind"] = kind
    for field, limit in TEXT_FIELDS.items():
        if field in meta:
            text = str(meta[field] or "").strip()
            if len(text) > limit:
                raise VaultError(f"{field} is too long (at most {limit} characters)")
            entry[field] = text
    if "port" in meta:
        port = meta["port"]
        if port not in (None, "") and not (str(port).isdigit() and 0 < int(port) < 65536):
            raise VaultError("port must be a number from 1 to 65535")
        entry["port"] = int(port) if port not in (None, "") else None
    if "used_by" in meta:
        entry["used_by"] = [str(u)[:40] for u in (meta["used_by"] or [])][:12]
    action = "updated"
    if value is not None:
        if not isinstance(value, str) or not value or len(value) > MAX_VALUE:
            raise VaultError(f"the value must be text, 1 to {MAX_VALUE} characters")
        entry["value"] = f.encrypt(value.encode("utf-8")).decode("ascii")
        entry["length"] = len(value)
        entry["generated"] = bool(meta.get("generated", False))
        action = "replaced" if name in data["credentials"] else "added"
    entry["updated"] = time.time()
    entry["updated_by"] = actor
    data["credentials"][name] = entry
    _save(data)
    _audit(action, name, actor)
    return _public(name, entry)


def generate(name: str, actor: str = "owner", nbytes: int = 32, **meta) -> dict:
    """Give a credential a new random value (for secrets this platform invents itself, like service tokens).
    Returns its details only: the new value is seen through reveal, never returned here."""
    if not 16 <= int(nbytes) <= 128:
        raise VaultError("nbytes must be 16 to 128")
    return set_credential(name, _random.token_hex(int(nbytes)), actor=actor, generated=True, **meta)


def delete_credential(name: str, actor: str = "owner") -> None:
    with _lock:
        _delete(name, actor)


def _delete(name: str, actor: str) -> None:
    _, data = _editable()
    if data["credentials"].pop(_check_name(name), None) is None:
        raise VaultError(f"{name} isn't in the vault")
    _save(data)
    _audit("deleted", name, actor)


def reveal(name: str, actor: str = "owner") -> str:
    """The plain value, for the owner's eyes in an admin client. The route in front of this checks the reveal key."""
    f = _fernet()
    entry = _load()["credentials"].get(_check_name(name))
    if f is None or entry is None or "value" not in entry:
        raise VaultError(f"{name} isn't in the vault")
    from cryptography.fernet import InvalidToken
    try:
        value = f.decrypt(entry["value"].encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise VaultError(f"{name} can't be decrypted with this VAULT_KEY") from None
    _audit("revealed", name, actor)
    return value


# Never handed out by name, whoever asks: the vault's own key and the server's owner token. secret() fell back to any
# environment variable, so a tool that takes a credential's name could have been asked for these (R&D's security
# review, 2026-10-01).
NEVER_BY_NAME = frozenset({"VAULT_KEY", "MCP_AUTH_TOKEN"})


def secret(name: str, default: str | None = None) -> str | None:
    """What a tool calls: the vault's value if it holds one, else the environment variable of the same name, else
    default. Never logs or returns anything to a caller outside this process, and never one of NEVER_BY_NAME.
    An install can map a tool's name onto the name it already stores the value under (VAULT_NAME_ALIASES in its own
    private_settings.py): the tool's name is tried first, then the older one."""
    value = _secret_one(name)
    if value is None:
        older = _name_aliases().get(str(name))
        value = _secret_one(older) if older else None
    return default if value is None else value


def _name_aliases() -> dict:
    try:
        from private_settings import VAULT_NAME_ALIASES
    except ImportError:
        return {}
    return VAULT_NAME_ALIASES if isinstance(VAULT_NAME_ALIASES, dict) else {}


def _secret_one(name: str) -> str | None:
    if str(name).upper() in NEVER_BY_NAME:
        return None
    f = _fernet()
    entry = _load()["credentials"].get(name) if f is not None else None
    if entry and "value" in entry:
        from cryptography.fernet import InvalidToken
        try:
            return f.decrypt(entry["value"].encode("ascii")).decode("utf-8")
        except InvalidToken:
            _logger.error("vault entry %s can't be decrypted: using the environment", name)
    return os.environ.get(name)


def audit_trail(limit: int = 50) -> list[dict]:
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _cli(argv: list[str]) -> int:
    """For deploy scripts on the server (2026-09-29): `python -m vault has NAME` exits 0 when NAME is stored, and
    `python -m vault set NAME [--label ..] [--kind ..] [--service ..]` stores the value read from stdin, so a value is
    never on a command line or anyone's screen. `python -m vault check NAME` reads a value from stdin and exits 0 if the
    vault holds the same one, 1 if not (R&D's R21: a pairing can exist on both sides and still differ). Prints details
    only, never a value."""
    import argparse
    import hmac
    import sys

    ap = argparse.ArgumentParser(prog="python -m vault")
    ap.add_argument("action", choices=("has", "set", "check"))
    ap.add_argument("name")
    ap.add_argument("--label")
    ap.add_argument("--kind", default="token")
    ap.add_argument("--service")
    args = ap.parse_args(argv)
    try:
        if args.action == "has":
            return 0 if _check_name(args.name) in _load()["credentials"] else 1
        if args.action == "check":
            held = secret(_check_name(args.name)) or ""
            same = bool(held) and hmac.compare_digest(held.encode(), sys.stdin.read().strip().encode())
            print(f"{args.name}: {'matches' if same else 'differs'}")
            return 0 if same else 1
        meta = {k: v for k, v in (("label", args.label), ("service", args.service)) if v is not None}
        info = set_credential(args.name, sys.stdin.read().strip(), actor="deploy", kind=args.kind, **meta)
    except VaultError as exc:
        print(f"vault: {exc}", file=sys.stderr)
        return 2
    print(f"{info['name']}: stored ({info['length']} characters)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))
