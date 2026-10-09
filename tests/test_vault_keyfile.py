"""The vault's key on the default install (2026-10-01): `uvx homeshed-mcp` over stdio has no .env and so no VAULT_KEY,
which left the vault off and a pasted Pro key nowhere to go. A key file in the data folder, made only by an explicit
`homeshed-mcp pro connect` (ensure_local_key), switches it on; VAULT_KEY in the environment always wins."""
import os

import pytest
from cryptography.fernet import Fernet

import mavis_pro
import vault


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault" / "vault.json")
    monkeypatch.setattr(vault, "AUDIT_FILE", tmp_path / "vault" / "audit.jsonl")
    monkeypatch.setattr(vault, "KEY_FILE", tmp_path / "vault" / "vault.key")
    monkeypatch.setattr(vault, "_cache", (None, None))
    return tmp_path


def test_no_key_anywhere_means_the_vault_is_off_and_nothing_is_created(isolated):
    assert not vault.enabled() and not vault.KEY_FILE.exists()
    with pytest.raises(vault.VaultError):
        vault.set_credential("SOME_KEY", "value", kind="api-key")


def test_ensure_local_key_switches_the_vault_on_and_values_round_trip(isolated):
    assert vault.ensure_local_key() and vault.KEY_FILE.is_file()
    if os.name != "nt":
        assert (vault.KEY_FILE.stat().st_mode & 0o777) == 0o600
    vault.set_credential("SOME_KEY", "s3cret-value", kind="api-key")
    assert vault.secret("SOME_KEY") == "s3cret-value"
    assert "s3cret-value" not in vault.VAULT_FILE.read_text(encoding="utf-8")  # stored encrypted


def test_a_key_is_never_replaced(isolated):
    vault.ensure_local_key()
    first = vault.KEY_FILE.read_text(encoding="utf-8")
    assert vault.ensure_local_key() and vault.KEY_FILE.read_text(encoding="utf-8") == first


def test_vault_key_in_the_environment_wins_and_no_file_is_made(isolated, monkeypatch):
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    assert vault.ensure_local_key() and not vault.KEY_FILE.exists()
    vault.KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    vault.KEY_FILE.write_text(Fernet.generate_key().decode(), encoding="utf-8")  # a different key on disk
    os.chmod(vault.KEY_FILE, 0o600)  # else POSIX refuses it for its mode first (the prepper's Linux run)
    vault.set_credential("SOME_KEY", "v", kind="api-key")
    monkeypatch.delenv("VAULT_KEY")
    with pytest.raises(vault.VaultError, match="can't be decrypted"):  # the file's key can't read the env key's value
        vault.reveal("SOME_KEY")
    assert vault.secret("SOME_KEY") is None


@pytest.mark.skipif(os.name == "nt", reason="file modes mean nothing on Windows (DPAPI seals the key there)")
def test_a_key_file_others_can_read_is_refused(isolated):
    vault.ensure_local_key()
    os.chmod(vault.KEY_FILE, 0o644)
    assert not vault.enabled()
    os.chmod(vault.KEY_FILE, 0o600)
    assert vault.enabled()


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows' own")
def test_on_windows_the_key_is_sealed_to_this_user(isolated):
    vault.ensure_local_key()
    text = vault.KEY_FILE.read_text(encoding="utf-8")
    assert text.startswith("dpapi:") and vault.enabled()
    vault.set_credential("SOME_KEY", "v", kind="api-key")
    assert vault.secret("SOME_KEY") == "v"


def test_the_key_file_stays_while_another_credential_needs_it(isolated):
    vault.ensure_local_key()
    vault.set_credential("OTHER_KEY", "v", kind="api-key")
    assert vault.forget_local_key_if_unused() is False and vault.KEY_FILE.exists()
    vault.delete_credential("OTHER_KEY", actor="test")
    assert vault.forget_local_key_if_unused() is True and not vault.KEY_FILE.exists()


def test_a_broken_key_file_leaves_the_vault_off(isolated):
    vault.KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    vault.KEY_FILE.write_text("not a fernet key", encoding="utf-8")
    os.chmod(vault.KEY_FILE, 0o600)  # so it's the content that's refused, not the mode
    assert not vault.enabled() and not vault.ensure_local_key()  # and it isn't replaced


def test_connecting_pro_with_the_vault_off_says_how_to_fix_it(isolated, monkeypatch):
    """Before: a VaultError is a ValueError, so the panel's route answered "Send the key as JSON"."""
    import httpx
    real = httpx.Client
    monkeypatch.setattr(mavis_pro.httpx, "get", lambda url, **k: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"client": {"name": "x"}}))).get(url, **k))
    monkeypatch.setattr(mavis_pro, "STATE_FILE", str(isolated / "mavis_pro.json"))
    with pytest.raises(mavis_pro.MavisProError, match="can't store it yet"):
        mavis_pro.connect_key("mav_" + "Qx7" * 10)
