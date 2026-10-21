"""A seat runner's client is rotated only by its manager script: a rotation anywhere else (the Control Panel's Rotate)
would end the runner's token and give it nothing (promoter, 2026-10-15)."""
import pytest

import clients


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(clients, "CLIENTS_FILE", tmp_path / "clients.json", raising=False)
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "audit.jsonl", raising=False)
    monkeypatch.setattr(clients, "_cache", (None, None), raising=False)
    clients.create("seat", "seat runner", "read")
    return clients


def test_a_managed_client_is_refused_unless_its_manager_rotates(store):
    store.set_managed_by("seat", "scripts/seat-runner-tokens.sh")
    with pytest.raises(clients.ClientError, match="seat-runner-tokens.sh"):
        store.rotate("seat")
    assert store.rotate("seat", by_manager=True).startswith(clients.TOKEN_PREFIX)
    row = next(c for c in store.list_clients()["clients"] if c["name"] == "seat")
    assert row["managed_by"] == "scripts/seat-runner-tokens.sh"


def test_clearing_the_mark_allows_rotate_again(store):
    store.set_managed_by("seat", "scripts/seat-runner-tokens.sh")
    store.set_managed_by("seat", None)
    assert store.rotate("seat")
