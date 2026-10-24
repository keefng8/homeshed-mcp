import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import library_signing as ls
import vault

SEED = bytes(range(32))
BODY = "Get-PSDrive -PSProvider FileSystem"


class Library:
    """A fake library: one script, and the signatures it was sent."""

    def __init__(self):
        self.script = {"id": "ops.disk-report", "version": 3, "title": "Disk report", "language": "powershell",
                       "runs_on": "windows", "risk": "read", "body": BODY, "sha256": ls.body_sha256(BODY),
                       "signed_by": []}
        self.signed = []

    def call(self, path, body=None):
        if body is None:
            assert path == "/publisher/scripts/ops.disk-report"
            return dict(self.script)
        assert path == "/publisher/signatures"
        self.signed.append(body)
        return {"id": body["id"], "version": self.script["version"]}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "STORE_FILE", tmp_path / "sign.json")
    secrets = {ls.KEY_NAME: ls.b64url(SEED)}
    monkeypatch.setattr(vault, "secret", lambda name: secrets.get(name))
    fake = Library()
    monkeypatch.setattr(ls, "_call", fake.call)
    fake.secrets = secrets
    return fake


def test_an_agent_only_asks_and_the_owners_yes_signs_what_the_runner_checks(lib):
    out = ls.request("ops.disk-report", "helper")
    assert out["status"].startswith("waiting for the owner") and lib.signed == []
    [row] = ls.waiting()
    assert (row["id"], row["body"], row["by"]) == ("ops.disk-report", BODY, "helper")
    assert ls.decide("ops.disk-report", "approve")["action"] == "approve"
    [sent] = lib.signed
    public = Ed25519PrivateKey.from_private_bytes(SEED).public_key()
    # the runner's message (homeshed_library.runner.publisher_message): verify raises if it doesn't match
    public.verify(ls.unb64url(sent["sig"]),
                  f"homeshed-publisher-v1\nops.disk-report\n{ls.body_sha256(BODY)}\nread".encode())
    assert sent["publisher"] == out["publisher"] and len(sent["publisher"]) == 19
    assert ls.waiting() == []


def test_off_without_a_key_in_the_vault(lib):
    del lib.secrets[ls.KEY_NAME]
    with pytest.raises(ls.SigningError, match="signing is off"):
        ls.request("ops.disk-report", "helper")
    assert ls.waiting() == []


def test_only_read_only_scripts_and_only_the_hash_worked_out_here(lib):
    lib.script["risk"] = "write"
    with pytest.raises(ls.SigningError, match="risk write"):
        ls.request("ops.disk-report", "helper")
    lib.script.update(risk="read", sha256="0" * 64)
    with pytest.raises(ls.SigningError, match="SHA-256"):
        ls.request("ops.disk-report", "helper")


def test_a_script_changed_since_the_request_isnt_signed(lib):
    ls.request("ops.disk-report", "helper")
    lib.script.update(body=BODY + "; Remove-Item x", sha256=ls.body_sha256(BODY + "; Remove-Item x"), version=4)
    with pytest.raises(ls.SigningError, match="changed since"):
        ls.decide("ops.disk-report", "approve")
    assert lib.signed == [] and ls.waiting() == []


def test_a_failed_send_leaves_the_request_waiting(lib, monkeypatch):
    ls.request("ops.disk-report", "helper")
    get = lib.call

    def refuse(path, body=None):
        if body is not None:
            raise ls.SigningError("the library answered 400")
        return get(path)
    monkeypatch.setattr(ls, "_call", refuse)
    with pytest.raises(ls.SigningError):
        ls.decide("ops.disk-report", "approve")
    assert [r["id"] for r in ls.waiting()] == ["ops.disk-report"]


def test_decline_drops_it_and_bad_ids_are_refused(lib):
    ls.request("ops.disk-report", "helper")
    assert ls.decide("ops.disk-report", "decline")["action"] == "decline"
    assert lib.signed == [] and ls.waiting() == []
    with pytest.raises(ls.SigningError, match="id"):
        ls.request("../admin", "helper")


def test_signed_already_doesnt_queue(lib):
    fp = ls.request("ops.disk-report", "helper")["publisher"]
    ls.decide("ops.disk-report", "decline")
    lib.script["signed_by"] = [fp]
    assert "already" in ls.request("ops.disk-report", "helper")["status"]
    assert ls.waiting() == []
