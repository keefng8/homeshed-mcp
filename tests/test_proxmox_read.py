"""proxmox.* read-only tools and the shared client. Proxmox is faked with httpx.MockTransport (_proxmox_fake.py):
no request leaves the process. The token values here are dummies; the point of several tests is that they never
appear in a result or an error."""
import httpx
import pytest
from _proxmox_fake import FakeProxmox

TOKEN_ID, TOKEN_SECRET = "root@pam!unittest", "dummy-secret-0000"  # secret-scan: allow (a dummy, never real)


@pytest.fixture
def fake(monkeypatch):
    from tools.proxmox import _client
    monkeypatch.delenv("VAULT_KEY", raising=False)  # vault.secret falls back to the environment
    for n in ("HOST", "VERIFY_TLS", "CA_PATH"):
        monkeypatch.delenv(f"PROXMOX_{n}", raising=False)
    monkeypatch.delenv("PROXMOX_API_TOKEN", raising=False)
    monkeypatch.delenv("PROXMOX_HOST", raising=False)
    monkeypatch.setenv("PROXMOX_HOST", "https://192.0.2.10:8006")  # a documentation address
    monkeypatch.setenv("PROXMOX_TOKEN_ID", TOKEN_ID)
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", TOKEN_SECRET)
    f = FakeProxmox()
    monkeypatch.setattr(_client, "_TRANSPORT", httpx.MockTransport(f))
    return f


def test_all_nine_tools_are_discovered():
    from registry import discover
    ids = {f"{c.category}.{c.name}" for c in discover() if c.category == "proxmox"}
    assert ids == {"proxmox.nodes.list", "proxmox.node.status", "proxmox.vms.list", "proxmox.vm.status",
                   "proxmox.storage.list", "proxmox.tasks.list", "proxmox.vm.power", "proxmox.vm.snapshot",
                   "proxmox.vm.create"}


def test_read_tools_are_read_risk_and_changes_write_risk():
    import manifest
    risks = {m["id"]: m["risk"] for m in manifest.load_manifests() if m["category"] == "proxmox"}
    assert {k for k, v in risks.items() if v == "write"} == {"proxmox.vm.power", "proxmox.vm.snapshot",
                                                            "proxmox.vm.create"}


def test_auth_header_and_default_host(fake, monkeypatch):
    from tools.proxmox import _client
    monkeypatch.delenv("PROXMOX_HOST")
    monkeypatch.setattr(_client, "DEFAULT_HOST", "https://192.0.2.10:8006")
    seen = {}

    def handler(request):
        seen["auth"], seen["url"] = request.headers["authorization"], str(request.url)
        return FakeProxmox._ok([])
    _client._TRANSPORT = httpx.MockTransport(handler)
    _client.get("/nodes")
    assert seen["auth"] == f"PVEAPIToken={TOKEN_ID}={TOKEN_SECRET}"
    assert seen["url"] == "https://192.0.2.10:8006/api2/json/nodes"


def test_no_host_at_all_names_the_setting(fake, monkeypatch):
    from tools.proxmox import _client
    monkeypatch.delenv("PROXMOX_HOST")
    monkeypatch.setattr(_client, "DEFAULT_HOST", "")
    with pytest.raises(_client.ProxmoxError, match="PROXMOX_HOST is not set"):
        _client.get("/nodes")
    assert fake.calls == []


def test_host_setting(fake, monkeypatch):
    from tools.proxmox import _client
    monkeypatch.setenv("PROXMOX_HOST", "https://pve.lan:8006/")
    assert _client.config()["base"] == "https://pve.lan:8006"
    monkeypatch.setenv("PROXMOX_HOST", "http://pve.lan:8006")
    with pytest.raises(_client.ProxmoxError, match="PROXMOX_HOST"):
        _client.config()


@pytest.mark.parametrize("value", ["false", "0", "true", ""])
def test_tls_is_always_checked(fake, monkeypatch, value):
    """The shipped code has no off switch: the old PROXMOX_VERIFY_TLS setting changes nothing."""
    from tools.proxmox import _client
    monkeypatch.setattr(_client, "UNCHECKED_HOSTS", frozenset())
    monkeypatch.setenv("PROXMOX_VERIFY_TLS", value)
    assert _client.config()["verify"] is True


def test_private_unchecked_host_and_ca_path_wins(fake, monkeypatch, tmp_path):
    from tools.proxmox import _client
    monkeypatch.setattr(_client, "UNCHECKED_HOSTS", frozenset({"https://192.0.2.10:8006"}))
    assert _client.config()["verify"] is not True
    ca = tmp_path / "ca.pem"
    ca.write_text("not a real cert")
    monkeypatch.setenv("PROXMOX_CA_PATH", str(ca))
    with pytest.raises(Exception):  # a CA file is used (and read) even for that host
        _client.config()


def test_ca_path_missing_file_is_named(fake, monkeypatch, tmp_path):
    from tools.proxmox import _client
    monkeypatch.setenv("PROXMOX_CA_PATH", str(tmp_path / "nope.pem"))
    with pytest.raises(_client.ProxmoxError, match="PROXMOX_CA_PATH"):
        _client.config()


@pytest.mark.parametrize("unset", [["PROXMOX_TOKEN_SECRET"], ["PROXMOX_TOKEN_ID"],
                                   ["PROXMOX_TOKEN_ID", "PROXMOX_TOKEN_SECRET"]])
def test_missing_token_names_the_setting_and_sends_nothing(fake, monkeypatch, unset):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.nodes_list import nodes_list
    for n in unset:
        monkeypatch.delenv(n)
    with pytest.raises(ProxmoxError) as exc:
        nodes_list()
    for n in unset:
        assert n in str(exc.value)
    assert TOKEN_SECRET not in str(exc.value) and TOKEN_ID not in str(exc.value)
    assert fake.calls == []


def test_badly_shaped_token_id_is_refused_without_showing_it(fake, monkeypatch):
    from tools.proxmox._client import ProxmoxError, get
    monkeypatch.setenv("PROXMOX_TOKEN_ID", "justaname")
    with pytest.raises(ProxmoxError, match="user@realm!tokenname") as exc:
        get("/nodes")
    assert "justaname" not in str(exc.value)


@pytest.mark.parametrize("code,match", [(401, "refused the API token"), (403, "may not do this"),
                                        (500, "HTTP 500")])
def test_http_errors_are_plain_and_never_hold_the_secret(fake, code, match):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.nodes_list import nodes_list
    fake.fail["/nodes"] = code
    with pytest.raises(ProxmoxError, match=match) as exc:
        nodes_list()
    assert TOKEN_SECRET not in str(exc.value)


def test_timeout_and_unreachable(fake):
    from tools.proxmox import _client

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    _client._TRANSPORT = httpx.MockTransport(slow)
    with pytest.raises(_client.ProxmoxError, match="didn't answer within 10 seconds"):
        _client.get("/nodes")

    def down(request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
    _client._TRANSPORT = httpx.MockTransport(down)
    with pytest.raises(_client.ProxmoxError, match="could not reach Proxmox"):
        _client.get("/nodes")

    def tls(request):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request)
    _client._TRANSPORT = httpx.MockTransport(tls)
    with pytest.raises(_client.ProxmoxError, match="PROXMOX_CA_PATH"):
        _client.get("/nodes")


def test_nodes_list_returns_every_cluster_node(fake):
    from tools.proxmox.nodes_list import nodes_list
    r = nodes_list()
    assert [n["node"] for n in r["nodes"]] == ["pve", "pve2"]
    assert r["nodes"][0] == {"node": "pve", "status": "online", "cpu_pct": 5.1, "cpus": 8, "mem_used_gib": 4.0,
                             "mem_total_gib": 16.0, "uptime_s": 3600}
    assert r["cluster"] == {"name": "homelab", "quorate": True, "nodes": 2}
    assert TOKEN_SECRET not in str(r)


def test_nodes_list_on_a_standalone_node_has_no_cluster(fake):
    from tools.proxmox.nodes_list import nodes_list
    fake.fail["/cluster/status"] = 403
    r = nodes_list()
    assert r["cluster"] is None and len(r["nodes"]) == 2


def test_node_status(fake):
    from tools.proxmox.node_status import node_status
    r = node_status("pve")
    assert r["cpu_pct"] == 25.0 and r["memory"] == {"used_gib": 2.0, "total_gib": 16.0}
    assert r["kernel"] == "6.8.12-1-pve" and r["pve_version"].startswith("pve-manager")


@pytest.mark.parametrize("bad", ["", "../etc", "pve/../x", "a b"])
def test_bad_node_names_are_refused_before_any_request(fake, bad):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.node_status import node_status
    with pytest.raises(ProxmoxError, match="node must be"):
        node_status(bad)
    assert fake.calls == []


def test_vms_list_is_cluster_wide_with_a_node_filter(fake):
    from tools.proxmox.vms_list import vms_list
    r = vms_list()
    assert [(g["vmid"], g["node"]) for g in r["guests"]] == [(101, "pve"), (200, "pve"), (300, "pve2")]
    assert r["summary"] == {"total": 3, "running": 2, "stopped": 1} and r["truncated"] is False
    assert [c[1] for c in fake.calls] == ["/cluster/resources"]  # one call for the whole cluster
    assert [g["vmid"] for g in vms_list(node="pve2")["guests"]] == [300]
    assert [g["kind"] for g in vms_list(kind="lxc")["guests"]] == ["lxc"]
    assert vms_list(node="other")["guests"] == []


def test_storage_list_is_cluster_wide(fake):
    from tools.proxmox.storage_list import storage_list
    assert [s["node"] for s in storage_list()["storage"]] == ["pve"]
    assert storage_list(node="pve2")["storage"] == []


def test_vm_status_finds_the_node_cluster_wide(fake):
    from tools.proxmox.vm_status import vm_status
    r = vm_status(300)
    assert r["node"] == "pve2" and r["name"] == "media"
    assert fake.calls[-1][1] == "/nodes/pve2/qemu/300/status/current"
    with pytest.raises(Exception, match="no VM or container with vmid 999"):
        vm_status(999)


def test_vm_status(fake):
    from tools.proxmox.vm_status import vm_status
    r = vm_status(101, node="pve")
    assert r["status"] == "running" and r["name"] == "web" and r["maxmem_gib"] == 2.0
    assert fake.calls[-1][1] == "/nodes/pve/qemu/101/status/current"


@pytest.mark.parametrize("vmid", [0, 99, -1, True, "101"])
def test_bad_vmid(fake, vmid):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_status import vm_status
    with pytest.raises(ProxmoxError, match="vmid"):
        vm_status(vmid, node="pve")


def test_storage_list(fake):
    from tools.proxmox.storage_list import storage_list
    r = storage_list()["storage"][0]
    assert r["storage"] == "local-lvm" and r["used_pct"] == 25.0 and r["total_gib"] == 100.0


def test_tasks_list(fake):
    from tools.proxmox.tasks_list import tasks_list
    r = tasks_list("pve", limit=2, errors_only=True, vmid=101)
    assert len(r["tasks"]) == 2 and r["tasks"][0]["status"] == "OK"
    assert r["tasks"][0]["started"].startswith("2026-")
    assert fake.calls[-1][2] == {"limit": "2", "errors": "1", "vmid": "101"}
    with pytest.raises(Exception, match="limit"):
        tasks_list("pve", limit=500)


def test_read_tools_never_post(fake):
    from tools.proxmox import (
        node_status,
        nodes_list,
        storage_list,
        tasks_list,
        vm_status,
        vms_list,
    )
    nodes_list.nodes_list(), node_status.node_status("pve"), vms_list.vms_list(), vm_status.vm_status(200, node="pve", kind="lxc")
    storage_list.storage_list(), tasks_list.tasks_list("pve")
    assert fake.posts() == []


# --- the token: one combined entry, or id + secret ----------------------------------------------------------------

COMBINED = f"{TOKEN_ID}={TOKEN_SECRET}"


def _header_seen(fake_client):
    from tools.proxmox import _client
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return FakeProxmox._ok([])
    _client._TRANSPORT = httpx.MockTransport(handler)
    _client.get("/nodes")
    return seen["auth"]


@pytest.mark.parametrize("stored", [COMBINED, f"PVEAPIToken={COMBINED}", f"pveapitoken={COMBINED}", f"  {COMBINED}  "])
def test_combined_token_in_token_id(fake, monkeypatch, stored):
    monkeypatch.delenv("PROXMOX_TOKEN_SECRET")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", stored)
    assert _header_seen(fake) == f"PVEAPIToken={TOKEN_ID}={TOKEN_SECRET}"


def test_a_secret_that_holds_an_equals_sign_splits_on_the_first_one(fake, monkeypatch):
    from tools.proxmox import _client
    monkeypatch.delenv("PROXMOX_TOKEN_SECRET")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", "root@pam!a=b=c")
    with pytest.raises(_client.ProxmoxError, match="no spaces, no '='"):
        _client.token()


def test_separate_secret_wins(fake, monkeypatch):
    from tools.proxmox import _client
    monkeypatch.setenv("PROXMOX_TOKEN_ID", f"PVEAPIToken={TOKEN_ID}")
    assert _client.token() == (TOKEN_ID, TOKEN_SECRET)


def test_combined_token_and_a_separate_secret_is_ambiguous(fake, monkeypatch):
    from tools.proxmox import _client
    monkeypatch.setenv("PROXMOX_TOKEN_ID", COMBINED)
    with pytest.raises(_client.ProxmoxError, match="keep one of them") as exc:
        _client.token()
    assert TOKEN_SECRET not in str(exc.value) and "unittest" not in str(exc.value)


def test_general_alias_proxmox_api_token(fake, monkeypatch):
    from tools.proxmox import _client
    monkeypatch.delenv("PROXMOX_TOKEN_ID")
    monkeypatch.delenv("PROXMOX_TOKEN_SECRET")
    monkeypatch.setenv("PROXMOX_API_TOKEN", COMBINED)
    assert _client.token() == (TOKEN_ID, TOKEN_SECRET)


def test_laptop_name_wins_over_the_alias(fake, monkeypatch):
    from tools.proxmox import _client
    monkeypatch.delenv("PROXMOX_TOKEN_SECRET")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", COMBINED)
    monkeypatch.setenv("PROXMOX_API_TOKEN", "other@pve!x=ffffffff-0000")  # secret-scan: allow (a dummy)
    assert _client.token() == (TOKEN_ID, TOKEN_SECRET)


@pytest.mark.parametrize("stored,match", [
    ("root@pam!unittest", "should hold the whole token"),          # no secret anywhere
    ("PVEAPIToken=", "should hold the whole token"),
    ("no-bang-here=dummy-secret-0000", "should hold the whole token"),
    ("rootpam!unittest=dummy-secret-0000", "user@realm!tokenname"),  # no realm
    ("root@pam!=dummy-secret-0000", "user@realm!tokenname"),         # empty token name
    ("root@pam!unittest=short", "UUID"),                             # secret too short
    ("root@pam!unit test=dummy-secret-0000", "user@realm!tokenname"),
    ("root@pam!unittest=dummy secret 0000", "UUID"),
])
def test_malformed_combined_tokens_name_the_setting_and_format_only(fake, monkeypatch, stored, match):
    from tools.proxmox import _client
    monkeypatch.delenv("PROXMOX_TOKEN_SECRET")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", stored)
    with pytest.raises(_client.ProxmoxError, match=match) as exc:
        _client.get("/nodes")
    msg = str(exc.value)
    assert "PROXMOX_TOKEN_ID" in msg
    for part in ("unittest", "dummy-secret", "short", "no-bang-here", "rootpam"):
        assert part not in msg
    assert fake.calls == []


def test_host_general_alias(fake, monkeypatch):
    from tools.proxmox import _client
    monkeypatch.delenv("PROXMOX_HOST")
    monkeypatch.setenv("PROXMOX_HOST", "https://192.0.2.20:8006")
    assert _client.config()["base"] == "https://192.0.2.20:8006"
    monkeypatch.setenv("PROXMOX_HOST", "https://192.0.2.30:8006")
    assert _client.config()["base"] == "https://192.0.2.30:8006"
