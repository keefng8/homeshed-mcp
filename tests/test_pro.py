"""pro.check: is this install on HomeShed Pro? The to-do list's gate (2026-09-30); test list drafted locally."""
from __future__ import annotations

import base64
import json
import sys
import time
import types

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import pro
import pro_proof

GONE = "Couldn't reach the Pro website just now."
TOKEN = "mav_" + "t" * 30  # secret-scan: allow (a dummy)
SITE_KEY = Ed25519PrivateKey.generate()


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def proof(token=TOKEN, key=SITE_KEY, iat=None) -> str:
    """A statement as the Pro site signs it (pro_sign.js), signed now unless iat says otherwise."""
    iat = int(time.time()) if iat is None else iat
    body = b64u(json.dumps({"aud": "homeshed", "tier": "pro", "sub": "u1", "th": pro_proof.token_hint(token),
                            "iat": iat, "exp": iat + 3600}).encode())
    return f"k1.{body}.{b64u(key.sign(body.encode()))}"


@pytest.fixture
def site(monkeypatch, tmp_path):
    """A stand-in mavis_pro whose status() answers (or raises) what the test sets, and whose last signed statement is
    signed when asked. The statement is kept in a temp folder, and read afresh as a new server would."""
    fake = types.SimpleNamespace(answer={}, TOKEN="MAVIS_PRO_TOKEN", vault=types.SimpleNamespace(secret=lambda n: TOKEN))

    def status():
        if isinstance(fake.answer, Exception):
            raise fake.answer
        return fake.answer

    fake.status = status
    fake.last_proof = lambda: proof()
    monkeypatch.setitem(sys.modules, "mavis_pro", fake)
    monkeypatch.setattr(pro_proof, "PUBLIC_KEYS", {"k1": b64u(SITE_KEY.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))})
    monkeypatch.setattr(pro, "STATE", tmp_path / "pro.json")
    monkeypatch.setattr(pro, "_confirmed_at", None)
    monkeypatch.setattr(pro, "_written_at", 0.0)
    return fake


def test_a_restart_keeps_the_grace(site, monkeypatch):
    """KB-0052: a redeploy while the Pro website answered 502 dropped the install to free."""
    site.answer = {"connected": True, "pro": True}
    assert pro.check()[0] and pro.STATE.exists()
    monkeypatch.setattr(pro, "_confirmed_at", None)  # a new server process: nothing in memory
    monkeypatch.setattr(pro, "_written_at", 0.0)
    site.answer = {"connected": True, "pro": False, "unreachable": True, "reason": GONE}
    assert pro.check() == (True, "")


def test_the_disk_hears_about_it_at_most_every_ten_minutes(site, monkeypatch):
    site.answer = {"connected": True, "pro": True}
    pro.check()
    first = pro.STATE.read_text(encoding="utf-8")
    pro.check()
    assert pro.STATE.read_text(encoding="utf-8") == first
    later = pro.time.time() + pro.WRITE_EVERY_S + 1
    monkeypatch.setattr(pro.time, "time", lambda: later)
    pro.check()
    assert pro.STATE.read_text(encoding="utf-8") != first


def test_an_unreadable_state_file_means_no_grace(site):
    pro.STATE.write_text("{not json", encoding="utf-8")
    site.answer = {"connected": True, "pro": False, "unreachable": True, "reason": GONE}
    assert pro.check() == (False, GONE)


def test_pro_when_the_membership_answers(site):
    site.answer = {"connected": True, "pro": True}
    assert pro.check() == (True, "")


def test_free_without_the_pro_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "mavis_pro", None)  # a build without it: importing it fails
    assert pro.check() == (False, "")


def test_free_when_not_connected_or_the_membership_ended(site):
    site.answer = {"connected": False, "pro": False}
    assert pro.check() == (False, "")
    site.answer = {"connected": True, "pro": False, "reason": "Your Pro membership has ended."}
    assert pro.check() == (False, "Your Pro membership has ended.")


def test_an_unreachable_site_keeps_a_recent_membership_for_a_week(site, monkeypatch):
    site.answer = {"connected": True, "pro": True}
    assert pro.check()[0]
    site.answer = {"connected": True, "pro": False, "unreachable": True, "reason": GONE}
    assert pro.check() == (True, "")
    later = pro.time.time() + pro.GRACE_S + 1
    monkeypatch.setattr(pro.time, "time", lambda: later)
    assert pro.check() == (False, GONE)


def test_an_unreachable_site_is_not_pro_without_a_recent_membership(site):
    site.answer = {"connected": True, "pro": False, "unreachable": True, "reason": GONE}
    assert pro.check() == (False, GONE)


def test_a_failing_check_never_raises(site):
    site.answer = RuntimeError("the vault is locked")
    assert pro.check() == (False, "Couldn't check the Pro membership just now.")


def test_a_lapsed_membership_ends_the_grace_at_once(site):
    site.answer = {"connected": True, "pro": True}
    pro.check()
    site.answer = {"connected": True, "pro": False, "reason": "Your Pro membership has ended."}
    assert pro.check() == (False, "Your Pro membership has ended.")  # an answer, not a blip: no grace


@pytest.mark.parametrize("state", [
    {"confirmed_at": 9999999999},                                   # to-do #19: a time typed into the file
    {"confirmed_at": time.time()},                                  # round 8: a recent one, with the site blocked
    {"proof": proof(key=Ed25519PrivateKey.generate())},             # signed by anyone but the Pro site
    {"proof": proof(token="mav_" + "o" * 30)},                      # copied from another install
    {"proof": proof(iat=int(time.time()) + 86400)},                 # signed "tomorrow"
    {"proof": proof()[:-4] + "AAAA"},                               # a genuine one, edited
])
def test_only_a_signed_statement_for_this_key_gives_grace(site, state):
    pro.STATE.write_text(json.dumps(state), encoding="utf-8")
    site.answer = {"connected": True, "pro": False, "unreachable": True, "reason": GONE}
    assert pro.check() == (False, GONE)


def test_a_genuine_statement_on_disk_gives_grace_after_a_restart(site):
    pro.STATE.write_text(json.dumps({"proof": proof(iat=int(time.time()) - 3 * 86400)}), encoding="utf-8")
    site.answer = {"connected": True, "pro": False, "unreachable": True, "reason": GONE}
    assert pro.check() == (True, "")  # signed 3 days ago, expired since: still within the week's grace
