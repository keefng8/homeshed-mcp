"""Signed Pro proofs: trust what is signed, not where it came from (to-do #19, round 5, 2026-10-08).

Closes the two bypasses still open after round 4: a fake Pro site (MAVIS_PRO_URL pointed at your own server) can make
status()["pro"] True, and a pack it serves is saved as source "pro" so its session notes switch on without asking.
With this, the Pro site signs (Ed25519) what it hands out and the install checks it against a public key shipped in
the code. A fake site has no private key, so it can grant nothing; only patched code can, which nothing client-side
can stop.

Three things are signed (the third on the build PC with a separate release key, 2026-10-14):
- each homeshed_pro wheel: "homeshed-pro|<version>|<sha256 of its RECORD>", checked before it's unpacked and again at
  every start (pro_plugin.py with pro_record.py), so a copied, edited or hand-made folder never loads.
- the membership statement, sent by GET /pro-api/whoami as {"proof": "<kid>.<payload>.<sig>"} (base64url parts).
  Payload: {"aud": "homeshed", "tier": "pro", "sub": <opaque subscriber id>, "th": <first 16 hex of sha256(token)>,
  "iat": <unix s>, "exp": <unix s>}. "th" ties the proof to this install's key, so a proof copied from someone else's
  install doesn't fit; "exp" keeps a replayed old proof short-lived. The 7-day grace runs from the signed iat.
- each served pack's top-level "issued" block (the watermark scheme):
  {"to": <opaque subscriber id, never an email>, "at": <unix s>, "kid": ..., "sig": <base64url>}, the signature over
  canonical(pack with issued.sig left out). A copied file still verifies (it's a real Pro pack, and "to" shows whose
  copy it was), but any edit to it breaks the signature.

Canonical JSON = sorted keys, no spaces, UTF-8 as is (json.dumps(sort_keys=True, separators=(",", ":"),
ensure_ascii=False)); pro_sign.js makes the same bytes. Packs must hold no floats (integers and strings only), since the
two languages print floats differently.

Needs only `cryptography`, already a dependency (the vault uses it).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# kid -> the raw 32-byte public key, base64url. Empty until the Pro site makes its key pair: then nothing verifies,
# which is the safe failure (no Pro status from the network, packs "unverified"). Keep old kids for one release after
# a key rotation so packs signed before it still check.
PUBLIC_KEYS: dict[str, str] = {"2026-10": "XbUljyTpDRSvazdlugwpNZL8ma_wiT0GUUIi4GXa5rE"}  # the Pro site's, 2026-10-14
# kid -> the release key's public half: it signs each homeshed_pro wheel on the build PC (scripts/pro-wheel-sign.py), so
# the Pro website only serves the signature and can't make one. Empty: no Pro package installs or loads. The private
# half lives only in the vault (PRO_RELEASE_KEY, scripts/pro-release-key.py), made 2026-10-14.
RELEASE_KEYS: dict[str, str] = {"2026-10": "fKtioax33r97PQLOvXdV3SZWNusyFlfR-fJV7M7saJk"}
AUD = "homeshed"
MAX_TTL_S = 24 * 3600   # a statement valid for longer than this is refused, whatever it says
SKEW_S = 300            # clock difference allowed between the site and this machine


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def token_hint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _check(kid, message: bytes, sig: str, keys) -> bool:
    raw = (keys if keys is not None else PUBLIC_KEYS).get(kid) if isinstance(kid, str) else None
    if not raw or not isinstance(sig, str):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(_b64d(raw)).verify(_b64d(sig), message)
        return True
    except (InvalidSignature, ValueError, binascii.Error):
        return False


def verify_membership(proof, token: str, keys=None, now: float | None = None) -> dict | None:
    """The signed payload if `proof` is a valid, current Pro statement for this token; None otherwise."""
    if not isinstance(proof, str) or proof.count(".") != 2:
        return None
    kid, body, sig = proof.split(".")
    if not _check(kid, body.encode("ascii", "replace"), sig, keys):
        return None
    try:
        p = json.loads(_b64d(body))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(p, dict):
        return None
    now = time.time() if now is None else now
    iat, exp = p.get("iat"), p.get("exp")
    if not (isinstance(iat, int) and isinstance(exp, int)):
        return None
    if p.get("aud") != AUD or p.get("tier") != "pro" or p.get("th") != token_hint(token):
        return None
    if iat > now + SKEW_S or exp < now - SKEW_S or exp - iat > MAX_TTL_S:
        return None
    return p


def signed_at(proof, token: str, keys=None) -> int | None:
    """When a genuine Pro statement for this token was signed (its iat), even if it has since expired: the 7-day grace
    while the Pro site can't be reached counts from here, not from a time in a file anyone can edit."""
    try:
        iat = json.loads(_b64d(proof.split(".")[1])).get("iat")
    except (AttributeError, IndexError, ValueError, binascii.Error):
        return None
    if not isinstance(iat, int):
        return None
    return iat if verify_membership(proof, token, keys, now=iat) else None


def wheel_message(version: str, record_sha256: str) -> bytes:
    return f"homeshed-pro|{version}|{record_sha256.lower()}".encode("ascii")


def verify_wheel(kid, sig, version: str, record_sha256: str, keys=None) -> bool:
    """True if the release key signed this Pro package version with this RECORD (the wheel's own list of every file
    and its hash). A fake site, a hacked one, or a hand-made folder has no release key."""
    if not isinstance(version, str) or not isinstance(record_sha256, str) or len(record_sha256) != 64:
        return False
    return _check(kid, wheel_message(version, record_sha256), sig, RELEASE_KEYS if keys is None else keys)


def verify_pack(pack, keys=None) -> dict | None:
    """The pack's "issued" block if its signature checks (an unedited Pro pack, wherever it came from); else None."""
    issued = pack.get("issued") if isinstance(pack, dict) else None
    if not isinstance(issued, dict):
        return None
    unsigned = dict(pack, issued={k: v for k, v in issued.items() if k != "sig"})
    return issued if _check(issued.get("kid"), canonical(unsigned), issued.get("sig"), keys) else None
