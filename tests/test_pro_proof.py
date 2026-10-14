"""Tests for pro_proof.py: the round-4 bypasses must fail, real signed answers must pass, and what pro_sign.js signs
must verify in Python (the canonical JSON must match). Writes only to pytest's tmp_path."""
import base64
import json
import os
import shutil
import subprocess
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import pro_proof as pp

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = "mav_" + "a" * 30
NOW = 1_791_500_000


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def keypair():
    k = Ed25519PrivateKey.generate()
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return k, b64u(raw)


def membership(k, kid="k1", **over):
    p = {"aud": "homeshed", "tier": "pro", "sub": "u1", "th": pp.token_hint(TOKEN), "iat": NOW, "exp": NOW + 3600}
    p.update(over)
    body = b64u(json.dumps(p).encode())
    return f"{kid}.{body}.{b64u(k.sign(body.encode()))}"


def signed_pack(k, kid="k1"):
    pack = {"slug": "token-saver", "version": 3, "guards": [{"id": "g1", "message": "Café ✓ «quoted»"}]}
    issued = {"to": "u1", "at": NOW, "kid": kid}
    sig = b64u(k.sign(pp.canonical(dict(pack, issued=issued))))
    return dict(pack, issued=dict(issued, sig=sig))


@pytest.fixture
def real():
    k, pub = keypair()
    return k, {"k1": pub}


def test_genuine_membership_passes(real):
    k, keys = real
    assert pp.verify_membership(membership(k), TOKEN, keys, now=NOW + 10)["sub"] == "u1"


# Round 4 bypass: MAVIS_PRO_URL -> a fake site answering 200. It can send no proof, junk, or one signed by its own key.
@pytest.mark.parametrize("proof", [None, "", "a.b.c", "k1.e30.AAAA"])
def test_fake_site_without_the_key_gets_nothing(real, proof):
    assert pp.verify_membership(proof, TOKEN, real[1], now=NOW) is None


def test_fake_site_signing_with_its_own_key_fails(real):
    fake, _ = keypair()
    assert pp.verify_membership(membership(fake), TOKEN, real[1], now=NOW) is None


def test_proof_copied_from_another_install_fails(real):
    k, keys = real
    assert pp.verify_membership(membership(k), "mav_" + "b" * 30, keys, now=NOW) is None


@pytest.mark.parametrize("over,now", [
    ({}, NOW + 3600 + pp.SKEW_S + 1),                  # replayed after it expired
    ({"iat": NOW + 3600}, NOW),                        # issued in the future (the round-2 lesson)
    ({"exp": NOW + pp.MAX_TTL_S + 1}, NOW),            # a statement that claims to last too long
    ({"tier": "free"}, NOW), ({"aud": "other-app"}, NOW), ({"iat": "1"}, NOW),
])
def test_bad_claims_fail(real, over, now):
    k, keys = real
    assert pp.verify_membership(membership(k, **over), TOKEN, keys, now=now) is None


def test_no_public_key_shipped_means_nothing_verifies(real):
    k, _ = real
    assert pp.verify_membership(membership(k), TOKEN, {}, now=NOW) is None
    assert pp.verify_pack(signed_pack(k), {}) is None


def test_genuine_pack_passes_and_names_its_subscriber(real):
    k, keys = real
    assert pp.verify_pack(signed_pack(k), keys)["to"] == "u1"


def test_pack_survives_a_json_round_trip_with_reordered_keys(real):
    k, keys = real
    p = signed_pack(k)
    p = json.loads(json.dumps(dict(reversed(list(p.items()))), indent=2))
    assert pp.verify_pack(p, keys) is not None


@pytest.mark.parametrize("edit", [
    lambda p: p["guards"][0].update(message="edited"),
    lambda p: p["issued"].update(to="someone-else"),   # scrubbing the watermark
    lambda p: p.pop("issued"),
    lambda p: p["issued"].update(kid="k2"),
])
def test_edited_or_unsigned_pack_fails(real, edit):
    k, keys = real
    p = signed_pack(k)
    edit(p)
    assert pp.verify_pack(p, keys) is None


def test_fake_site_pack_fails(real):
    fake, _ = keypair()
    assert pp.verify_pack(signed_pack(fake), real[1]) is None


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_node_signer_verifies_in_python(real, tmp_path):
    k, keys = real
    pem = tmp_path / "k.pem"
    pem.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    js = os.path.join(HERE, "pro_sign.js")
    now = str(int(time.time()))
    proof = subprocess.run(["node", js, str(pem), "k1", "membership", TOKEN, "u7", now],
                           capture_output=True, text=True, check=True).stdout
    assert pp.verify_membership(proof, TOKEN, keys)["sub"] == "u7"
    raw = {"slug": "safe-operator", "version": 1, "guards": [{"message": "Café ✓ «quoted»   end", "n": [3, 1]}]}
    src = tmp_path / "p.json"
    src.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    out = subprocess.run(["node", js, str(pem), "k1", "pack", str(src), "u7", now],
                         capture_output=True, check=True).stdout
    assert pp.verify_pack(json.loads(out.decode("utf-8")), keys)["to"] == "u7"
