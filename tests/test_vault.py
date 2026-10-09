"""Credentials vault: values are encrypted at rest, never listed, never audited, only revealed with the reveal key,
and tools fall back to the environment so nothing breaks while the vault is empty or off. Test list drafted by the
local model (qwen3-coder-30b), then reviewed."""
import asyncio
import io
import json
import os
import sys

import pytest
from cryptography.fernet import Fernet

import vault

FAKE = "nvapi-" + "Zz9" * 12   # built at run time: no literal token in this file


@pytest.fixture(autouse=True)
def fresh_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault.json")
    monkeypatch.setattr(vault, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(vault, "_cache", (None, None))
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    return tmp_path


def test_a_value_is_encrypted_on_disk_and_never_listed(fresh_vault):
    vault.set_credential("NVIDIA_API_KEY", FAKE, label="NVIDIA cloud AI", kind="api-key", service="ai")
    assert FAKE not in (fresh_vault / "vault.json").read_text(encoding="utf-8")
    [entry] = vault.list_credentials()
    assert entry["name"] == "NVIDIA_API_KEY" and entry["length"] == len(FAKE) and "value" not in entry
    assert FAKE not in json.dumps(vault.list_credentials())


def test_tools_read_the_vault_first_then_the_environment(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "from-env")
    assert vault.secret("NVIDIA_API_KEY") == "from-env"
    vault.set_credential("NVIDIA_API_KEY", FAKE)
    assert vault.secret("NVIDIA_API_KEY") == FAKE
    vault.delete_credential("NVIDIA_API_KEY")
    assert vault.secret("NVIDIA_API_KEY") == "from-env"
    assert vault.secret("NOT_THERE", "fallback") == "fallback"


def test_the_vault_key_and_the_owner_token_are_never_handed_out_by_name(monkeypatch):
    """R&D's security review, 2026-10-01: secret() fell back to any environment variable, the vault's own key
    included, so a tool that takes a credential's name could have been asked for it."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")  # secret-scan: allow (fake)
    for name in ("VAULT_KEY", "vault_key", "MCP_AUTH_TOKEN"):
        assert vault.secret(name) is None and vault.secret(name, "fallback") == "fallback"
    assert os.environ["VAULT_KEY"]  # it's there: it's just never given out


def test_without_a_key_the_vault_is_off_but_nothing_breaks(monkeypatch):
    vault.set_credential("NVIDIA_API_KEY", FAKE)
    monkeypatch.delenv("VAULT_KEY")
    monkeypatch.setenv("NVIDIA_API_KEY", "from-env")
    assert not vault.enabled()
    assert vault.secret("NVIDIA_API_KEY") == "from-env"
    with pytest.raises(vault.VaultError, match="VAULT_KEY"):
        vault.set_credential("OTHER_KEY", "x" * 20)


def test_a_wrong_key_falls_back_instead_of_crashing(monkeypatch):
    vault.set_credential("NVIDIA_API_KEY", FAKE)
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("NVIDIA_API_KEY", "from-env")
    assert vault.secret("NVIDIA_API_KEY") == "from-env"
    with pytest.raises(vault.VaultError, match="decrypted"):
        vault.reveal("NVIDIA_API_KEY")


def test_a_malformed_key_turns_the_vault_off(monkeypatch):
    monkeypatch.setenv("VAULT_KEY", "not-a-fernet-key")
    assert not vault.enabled()


def test_the_audit_trail_never_holds_a_value(fresh_vault):
    vault.set_credential("NVIDIA_API_KEY", FAKE)
    vault.set_credential("NVIDIA_API_KEY", FAKE + "2")
    vault.reveal("NVIDIA_API_KEY")
    vault.delete_credential("NVIDIA_API_KEY")
    text = (fresh_vault / "audit.jsonl").read_text(encoding="utf-8")
    assert FAKE not in text
    assert [a["action"] for a in vault.audit_trail()] == ["deleted", "revealed", "replaced", "added"]


def test_generate_makes_a_random_value_that_only_reveal_shows():
    out = vault.generate("GPU_SERVICE_TOKEN", nbytes=32, label="GPU helper token")
    assert out["generated"] and out["length"] == 64 and "value" not in out
    first = vault.reveal("GPU_SERVICE_TOKEN")
    vault.generate("GPU_SERVICE_TOKEN")
    assert len(first) == 64 and vault.reveal("GPU_SERVICE_TOKEN") != first
    with pytest.raises(vault.VaultError, match="nbytes"):
        vault.generate("GPU_SERVICE_TOKEN", nbytes=4)


@pytest.mark.parametrize("name", ["lower_case", "1STARTS_WITH_DIGIT", "HAS-DASH", "A", "X" * 65, "../ETC"])
def test_names_are_plain_capitals(name):
    with pytest.raises(vault.VaultError, match="CAPITALS"):
        vault.set_credential(name, "x" * 20)


def test_missing_entries_give_a_plain_error():
    with pytest.raises(vault.VaultError, match="isn't in the vault"):
        vault.reveal("NOT_THERE")
    with pytest.raises(vault.VaultError, match="isn't in the vault"):
        vault.delete_credential("NOT_THERE")


def test_details_are_checked_and_can_change_without_the_value():
    with pytest.raises(vault.VaultError, match="kind"):
        vault.set_credential("SSH_DOCKER_HOST", "k" * 30, kind="banana")
    with pytest.raises(vault.VaultError, match="port"):
        vault.set_credential("SSH_DOCKER_HOST", "k" * 30, kind="ssh-key", port=70000)
    with pytest.raises(vault.VaultError, match="1 to"):
        vault.set_credential("SSH_DOCKER_HOST", "")
    vault.set_credential("SSH_DOCKER_HOST", "k" * 30, kind="ssh-key", host="192.168.1.10", port=22, username="root")
    vault.set_credential("SSH_DOCKER_HOST", note="server deploy key")
    [entry] = vault.list_credentials()
    assert entry["note"] == "server deploy key" and entry["port"] == 22 and vault.secret("SSH_DOCKER_HOST") == "k" * 30
    with pytest.raises(vault.VaultError, match="give it a value"):
        vault.set_credential("NEW_ONE", note="no value yet")


@pytest.mark.skipif(sys.platform == "win32", reason="file modes are POSIX")
def test_the_file_is_readable_by_its_owner_only(fresh_vault):
    vault.set_credential("NVIDIA_API_KEY", FAKE)
    assert oct(os.stat(fresh_vault / "vault.json").st_mode & 0o777) == "0o600"


# --- routes -------------------------------------------------------------------------------------------------------

@pytest.fixture()
def server(monkeypatch):
    import importlib
    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")  # secret-scan: allow (fake)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("VAULT_REVEAL_KEY", "reveal-secret")  # secret-scan: allow (fake)
    sys.modules.pop("server", None)
    return importlib.import_module("server")


class Req:
    def __init__(self, name="NVIDIA_API_KEY", body=None, headers=None):
        self.path_params = {"name": name}
        self._body = body
        self.headers = {"content-length": "1" if body is not None else "0", **(headers or {})}

    async def json(self):
        return self._body


def call(handler, req):
    resp = asyncio.run(handler(req))
    return resp.status_code, json.loads(resp.body)


def test_routes_add_list_and_never_return_the_value(server):
    status, body = call(server.vault_set, Req(body={"value": FAKE, "label": "NVIDIA", "kind": "api-key"}))
    assert status == 200 and "value" not in body
    status, body = call(server.vault_list, Req())
    assert body["enabled"] and body["reveal"] and FAKE not in json.dumps(body)
    status, body = call(server.vault_generate, Req(name="GPU_SERVICE_TOKEN", body={"bytes": 32}))
    assert status == 200 and "value" not in body and body["length"] == 64


def test_reveal_needs_the_reveal_key(server):
    call(server.vault_set, Req(body={"value": FAKE}))
    assert call(server.vault_reveal, Req())[0] == 403
    assert call(server.vault_reveal, Req(headers={"x-vault-reveal": "wrong"}))[0] == 403
    status, body = call(server.vault_reveal, Req(headers={"x-vault-reveal": "reveal-secret"}))
    assert status == 200 and body["value"] == FAKE


def test_route_errors_are_plain_and_hold_no_value(server):
    status, body = call(server.vault_set, Req(name="bad name", body={"value": FAKE}))
    assert status == 400 and "CAPITALS" in body["error"] and FAKE not in body["error"]
    status, body = call(server.vault_test, Req(body={"preset": "openai", "url": "ftp://x"}))
    assert status == 400 and "http" in body["error"]


def test_client_tokens_cannot_reach_the_vault(server, monkeypatch):
    """RequestGuard: a client token only ever reaches /mcp."""
    reached = []

    async def app(scope, receive, send):
        reached.append(True)

    monkeypatch.setattr(server.clients, "resolve", lambda auth: ("friend", "ok"))
    guard = server.RequestGuard(app, "owner-secret", ["localhost"])
    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": "/vault", "headers": [(b"host", b"localhost"), (b"authorization", b"Bearer x")]}
    asyncio.run(guard(scope, None, send))
    assert not reached and sent[0]["status"] == 403


# --- R&D F1 (2026-09-30): a saved key only goes where it was saved for. Test list drafted by local_ai.ask. -----------

@pytest.fixture()
def outbound(monkeypatch):
    """Every request /vault-test sends: (url, headers). Nothing leaves the test."""
    import httpx
    calls = []

    class Resp:
        status_code, headers = 200, {"content-type": "application/json"}

        def json(self):
            return {"data": [{"id": "m1"}]}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            calls.append((url, dict(headers or {})))
            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return calls


def test_a_saved_key_never_goes_to_an_address_the_caller_picks(server, outbound):
    vault.set_credential("OPENAI_KEY", FAKE, host="models.example.com")
    vault.set_credential("NVIDIA_API_KEY", FAKE)
    for body in ({"preset": "openai", "name": "OPENAI_KEY", "url": "https://listener.example.net/v1"},
                 {"preset": "nvidia", "name": "NVIDIA_API_KEY", "url": "https://listener.example.net/v1"},
                 {"preset": "openai", "name": "NVIDIA_API_KEY", "url": "https://listener.example.net/v1"}):
        status, answer = call(server.vault_test, Req(body=body))
        assert status == 403 and "saved for" in answer["error"] and FAKE not in json.dumps(answer)
    assert outbound == []


def test_a_saved_key_is_tested_where_it_was_saved_for(server, outbound):
    vault.set_credential("OPENAI_KEY", FAKE, host="models.example.com")
    vault.set_credential("GPU_KEY", FAKE, host="http://gpu.lan", port=8080)
    vault.set_credential("NVIDIA_API_KEY", FAKE)
    assert call(server.vault_test, Req(body={"preset": "openai", "name": "OPENAI_KEY",
                                             "url": "https://models.example.com/v1"}))[1]["ok"] is True
    assert call(server.vault_test, Req(body={"preset": "openai", "name": "GPU_KEY",
                                             "url": "http://gpu.lan:8080/v1"}))[1]["ok"] is True
    assert call(server.vault_test, Req(body={"preset": "nvidia", "name": "NVIDIA_API_KEY"}))[1]["ok"] is True
    assert call(server.vault_test, Req(body={"preset": "openai", "name": "GPU_KEY",
                                             "url": "http://gpu.lan:9999/v1"}))[0] == 403  # the wrong port
    assert [u for u, _ in outbound] == ["https://models.example.com/v1/models", "http://gpu.lan:8080/v1/models",
                                        server.NVIDIA_URL + "/models"]
    assert all(h == {"Authorization": f"Bearer {FAKE}"} for _, h in outbound)


def test_a_typed_key_or_no_key_may_go_anywhere_and_the_reveal_key_opens_the_rest(server, outbound):
    vault.set_credential("OPENAI_KEY", FAKE, host="models.example.com")
    typed = "typed-" + "k" * 20
    assert call(server.vault_test, Req(body={"preset": "openai", "key": typed, "url": "https://a.example.net"}))[0] == 200
    assert call(server.vault_test, Req(body={"preset": "openai", "url": "https://b.example.net"}))[0] == 200
    assert call(server.vault_test, Req(body={"preset": "openai", "name": "OPENAI_KEY", "url": "https://c.example.net"},
                                       headers={"x-vault-reveal": "reveal-secret"}))[0] == 200
    assert outbound == [("https://a.example.net/models", {"Authorization": f"Bearer {typed}"}),
                        ("https://b.example.net/models", {}),
                        ("https://c.example.net/models", {"Authorization": f"Bearer {FAKE}"})]


def test_a_name_not_in_the_vault_never_reads_the_environment(server, outbound):
    """vault.secret falls back to the environment: a name like MCP_AUTH_TOKEN must not ride along to NVIDIA."""
    status, answer = call(server.vault_test, Req(body={"preset": "nvidia", "name": "MCP_AUTH_TOKEN"}))
    assert status == 400 and "isn't in the vault" in answer["error"] and outbound == []


def test_wrong_reveal_keys_are_limited_and_audited(server, fresh_vault):
    vault.set_credential("NVIDIA_API_KEY", FAKE)
    for _ in range(5):
        assert call(server.vault_reveal, Req(headers={"x-vault-reveal": "guess"}))[0] == 403
    assert call(server.vault_reveal, Req(headers={"x-vault-reveal": "reveal-secret"}))[0] == 429  # locked for now
    audit = (fresh_vault / "audit.jsonl").read_text(encoding="utf-8")
    assert audit.count("wrong reveal key") == 5 and FAKE not in audit and "guess" not in audit


def test_no_key_at_all_is_not_counted_as_a_guess(server):
    vault.set_credential("NVIDIA_API_KEY", FAKE)
    for _ in range(8):
        assert call(server.vault_reveal, Req())[0] == 403
    assert call(server.vault_reveal, Req(headers={"x-vault-reveal": "reveal-secret"}))[0] == 200


def test_healthz_answers_without_a_token_and_nothing_else_does(server):
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["path"])

    guard = server.RequestGuard(app, "owner-secret", ["localhost"])
    sent = []

    async def send(message):
        sent.append(message)

    for path in ("/healthz", "/capabilities"):
        asyncio.run(guard({"type": "http", "method": "GET", "path": path, "headers": [(b"host", b"localhost")]},
                          None, send))
    assert reached == ["/healthz"] and sent[0]["status"] == 401
    asyncio.run(guard({"type": "http", "method": "GET", "path": "/healthz", "headers": [(b"host", b"evil")]},
                      None, send))
    assert reached == ["/healthz"] and sent[-2]["status"] == 421  # the Host check still applies


@pytest.mark.parametrize("route", ["clients_create", "projects_register", "projects_remove"])
def test_a_json_list_body_is_a_400_not_a_crash(server, route):
    status, answer = call(getattr(server, route), Req(body=[1, 2]))
    assert status == 400 and "JSON object" in answer["error"]


def test_the_cli_stores_from_stdin_and_never_prints_the_value(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE + "\n"))
    assert vault._cli(["set", "STUDIO_SERVICE_TOKEN", "--label", "Video studio voice access"]) == 0
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err and "stored" in out.out
    assert vault.secret("STUDIO_SERVICE_TOKEN") == FAKE
    assert vault._cli(["has", "STUDIO_SERVICE_TOKEN"]) == 0 and vault._cli(["has", "NOT_THERE"]) == 1
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert vault._cli(["set", "EMPTY_ONE"]) == 2


def test_the_cli_check_says_only_matches_or_differs(monkeypatch, capsys):
    """R21: the studio's pairing can exist on both sides and still differ; `check` compares without printing either."""
    vault.set_credential("STUDIO_SERVICE_TOKEN", FAKE, kind="token")
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE + "\n"))
    assert vault._cli(["check", "STUDIO_SERVICE_TOKEN"]) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO("something-else"))
    assert vault._cli(["check", "STUDIO_SERVICE_TOKEN"]) == 1
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE))
    assert vault._cli(["check", "NOT_STORED_HERE"]) == 1
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err and "matches" in out.out and "differs" in out.out


# --- name aliases (2026-10-06): a tool's public name, then the name an install already stores it under ----------

@pytest.fixture
def aliases(monkeypatch):
    table = {"SHOP_TOKEN": "OLD_SHOP_TOKEN", "SAFE_NAME": "VAULT_KEY"}
    monkeypatch.setattr(vault, "_name_aliases", lambda: table)
    for name in ("SHOP_TOKEN", "OLD_SHOP_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return table


def test_the_public_name_wins_when_both_are_set(aliases):
    vault.set_credential("SHOP_TOKEN", "new-value", kind="token")
    vault.set_credential("OLD_SHOP_TOKEN", "old-value", kind="token")
    assert vault.secret("SHOP_TOKEN") == "new-value"


def test_the_older_name_is_used_when_only_it_is_set(aliases, monkeypatch):
    vault.set_credential("OLD_SHOP_TOKEN", "old-value", kind="token")
    assert vault.secret("SHOP_TOKEN") == "old-value"
    vault.delete_credential("OLD_SHOP_TOKEN") if hasattr(vault, "delete_credential") else None
    monkeypatch.setenv("OLD_SHOP_TOKEN", "from-env")
    assert vault.secret("SHOP_TOKEN") in ("old-value", "from-env")


def test_neither_set_gives_the_default(aliases):
    assert vault.secret("SHOP_TOKEN") is None and vault.secret("SHOP_TOKEN", "d") == "d"


def test_no_alias_table_behaves_as_before(monkeypatch):
    monkeypatch.setattr(vault, "_name_aliases", lambda: {})
    monkeypatch.setenv("OLD_SHOP_TOKEN", "x")
    assert vault.secret("SHOP_TOKEN", "d") == "d"


def test_an_alias_never_reaches_a_denied_name(aliases):
    assert vault.secret("SAFE_NAME", "d") == "d"  # VAULT_KEY is set by the fixture, and stays unreachable
