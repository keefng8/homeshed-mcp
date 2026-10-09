"""proxmox.vm.power / .vm.snapshot / .vm.create and the owner-approval queue (proxmox_pending.py). Proxmox is faked
(httpx.MockTransport): a dry run or a queued change must never send a POST; only an owner approval may, once."""
import json
import time

import httpx
import pytest
from _proxmox_fake import FakeProxmox


@pytest.fixture
def fake(monkeypatch, tmp_path):
    import clients
    import proxmox_pending
    from tools.proxmox import _client
    monkeypatch.delenv("VAULT_KEY", raising=False)
    for n in ("HOST", "VERIFY_TLS", "CA_PATH"):
        monkeypatch.delenv(f"PROXMOX_{n}", raising=False)
    monkeypatch.delenv("PROXMOX_API_TOKEN", raising=False)
    monkeypatch.delenv("PROXMOX_HOST", raising=False)
    monkeypatch.setenv("PROXMOX_HOST", "https://192.0.2.10:8006")  # a documentation address
    monkeypatch.setenv("PROXMOX_TOKEN_ID", "root@pam!unittest")
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "dummy-secret-0000")
    monkeypatch.setattr(proxmox_pending, "PENDING_FILE", tmp_path / "proxmox_pending.json")
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "audit.jsonl")
    f = FakeProxmox()
    monkeypatch.setattr(_client, "_TRANSPORT", httpx.MockTransport(f))
    return f


def test_dry_run_is_the_default_and_never_posts(fake):
    from tools.proxmox.vm_power import vm_power
    r = vm_power(101, node="pve", action="shutdown")
    assert r == {"dry_run": True, "change": "shutdown qemu 101 on pve",
                 "checks": {"name": "web", "status_now": "running", "lock": None}}
    assert fake.posts() == []


def test_dry_run_notes_a_no_op_and_warns_on_hard_stop(fake):
    from tools.proxmox.vm_power import vm_power
    assert "already running" in vm_power(101, node="pve", action="start")["checks"]["note"]
    assert "hard power-off" in vm_power(101, node="pve", action="stop")["checks"]["warning"]


def test_dry_run_false_only_queues(fake):
    import proxmox_pending
    from tools.proxmox.vm_power import vm_power
    r = vm_power(200, node="pve", action="start", kind="lxc", dry_run=False)
    assert r["pending"] is True and len(r["id"]) == 8 and "nothing has changed" in r["how"]
    assert fake.posts() == []
    [item] = proxmox_pending.list_pending()
    assert item["action"] == {"op": "power", "node": "pve", "kind": "lxc", "vmid": 200, "action": "start"}
    assert item["client"] == "owner"


def test_approve_runs_it_once_then_its_gone(fake):
    import proxmox_pending
    from tools.proxmox.vm_power import vm_power
    pid = vm_power(101, node="pve", action="reboot", dry_run=False)["id"]
    out = proxmox_pending.decide(pid, approve=True)
    assert out["result"]["upid"] == "UPID:pve:00001234:task"
    assert [(m, p) for m, p, _, _ in fake.posts()] == [("POST", "/nodes/pve/qemu/101/status/reboot")]
    assert proxmox_pending.list_pending() == []
    with pytest.raises(proxmox_pending.PendingError, match="no Proxmox change"):
        proxmox_pending.decide(pid, approve=True)
    assert len(fake.posts()) == 1


def test_decline_drops_it_without_a_request(fake):
    import proxmox_pending
    from tools.proxmox.vm_snapshot import vm_snapshot
    pid = vm_snapshot(101, node="pve", snapname="before-upgrade", dry_run=False)["id"]
    assert proxmox_pending.decide(pid, approve=False) == {"id": pid, "approved": False,
                                                          "change": "snapshot qemu 101 on pve as 'before-upgrade'"}
    assert fake.posts() == [] and proxmox_pending.list_pending() == []


def test_snapshot_posts_name_and_description(fake):
    import proxmox_pending
    from tools.proxmox.vm_snapshot import vm_snapshot
    pid = vm_snapshot(101, node="pve", snapname="pre_v2", description="before v2", dry_run=False)["id"]
    proxmox_pending.decide(pid, approve=True)
    [(_, path, _, body)] = fake.posts()
    assert path == "/nodes/pve/qemu/101/snapshot" and "snapname=pre_v2" in body and "description=before+v2" in body


@pytest.mark.parametrize("name", ["1abc", "a", "has space", "x" * 41, "semi;colon"])
def test_bad_snapshot_names(fake, name):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_snapshot import vm_snapshot
    with pytest.raises(ProxmoxError, match="snapname"):
        vm_snapshot(101, node="pve", snapname=name)


def test_failed_approval_keeps_it_waiting_with_the_error(fake):
    import proxmox_pending
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_power import vm_power
    pid = vm_power(101, node="pve", action="shutdown", dry_run=False)["id"]
    fake.fail["/nodes/pve/qemu/101/status/shutdown"] = 500
    with pytest.raises(ProxmoxError, match="HTTP 500"):
        proxmox_pending.decide(pid, approve=True)
    [item] = proxmox_pending.list_pending()
    assert "HTTP 500" in item["last_error"]


def test_a_guest_locked_since_queueing_is_refused_on_approval(fake):
    import proxmox_pending
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_power import vm_power
    pid = vm_power(101, node="pve", action="shutdown", dry_run=False)["id"]
    fake.lock[101] = "backup"
    with pytest.raises(ProxmoxError, match="locked"):
        proxmox_pending.decide(pid, approve=True)
    assert fake.posts() == []


def test_expired_changes_cant_be_approved(fake, monkeypatch):
    import proxmox_pending
    from tools.proxmox.vm_power import vm_power
    pid = vm_power(101, node="pve", action="shutdown", dry_run=False)["id"]
    real = time.time
    monkeypatch.setattr(proxmox_pending.time, "time", lambda: real() + proxmox_pending.TTL_S + 1)
    with pytest.raises(proxmox_pending.PendingError):
        proxmox_pending.decide(pid, approve=True)
    assert fake.posts() == []


def test_a_tampered_queue_entry_is_revalidated(fake):
    import proxmox_pending
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_power import vm_power
    pid = vm_power(101, node="pve", action="shutdown", dry_run=False)["id"]
    data = json.loads(proxmox_pending.PENDING_FILE.read_text())
    data["items"][0]["action"]["action"] = "destroy"
    proxmox_pending.PENDING_FILE.write_text(json.dumps(data))
    with pytest.raises(ProxmoxError, match="action must be"):
        proxmox_pending.decide(pid, approve=True)
    assert fake.posts() == []


def test_unreadable_queue_refuses(fake):
    import proxmox_pending
    from tools.proxmox.vm_power import vm_power
    proxmox_pending.PENDING_FILE.write_text("{not json")
    with pytest.raises(proxmox_pending.PendingError, match="unreadable"):
        vm_power(101, node="pve", action="shutdown", dry_run=False)


def test_queue_is_bounded(fake, monkeypatch):
    import proxmox_pending
    from tools.proxmox.vm_power import vm_power
    monkeypatch.setattr(proxmox_pending, "MAX_TOTAL", 2)
    vm_power(101, node="pve", action="shutdown", dry_run=False), vm_power(101, node="pve", action="reboot", dry_run=False)
    with pytest.raises(proxmox_pending.PendingError, match="already waiting"):
        vm_power(101, node="pve", action="start", dry_run=False)


@pytest.mark.parametrize("action", ["destroy", "delete", "rollback", "", "migrate"])
def test_only_the_four_power_actions(fake, action):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_power import vm_power
    with pytest.raises(ProxmoxError, match="action must be"):
        vm_power(101, node="pve", action=action)


def test_wrong_kind_or_node_for_the_vmid(fake):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_power import vm_power
    with pytest.raises(ProxmoxError, match="is a qemu on pve, not a lxc"):
        vm_power(101, node="pve", action="start", kind="lxc")
    with pytest.raises(ProxmoxError, match="no node called"):
        vm_power(101, node="nope", action="start")
    with pytest.raises(ProxmoxError, match="no qemu with vmid 999"):
        vm_power(999, node="pve", action="start")


def test_create_dry_run_then_approved(fake):
    import proxmox_pending
    from tools.proxmox.vm_create import vm_create
    opts = {"name": "new", "memory": 2048, "cores": 2, "net0": "virtio,bridge=vmbr0", "onboot": True}
    r = vm_create("pve", 150, "qemu", opts)
    assert r["dry_run"] is True and r["checks"] == {"vmid_free": True} and r["options"]["onboot"] == 1
    assert fake.posts() == []
    pid = vm_create("pve", 150, "qemu", opts, dry_run=False)["id"]
    proxmox_pending.decide(pid, approve=True)
    [(_, path, _, body)] = fake.posts()
    assert path == "/nodes/pve/qemu" and "vmid=150" in body and "memory=2048" in body


def test_create_never_reuses_a_vmid(fake):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_create import vm_create
    with pytest.raises(ProxmoxError, match="already used"):
        vm_create("pve", 101, "qemu", {"name": "clash"})


@pytest.mark.parametrize("key", ["force", "archive", "unique", "delete", "purge", "password", "cipassword",
                                 "api-token", "my_secret", "vmid"])
def test_create_refuses_dangerous_or_secret_options(fake, key):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_create import vm_create
    with pytest.raises(ProxmoxError, match="isn't allowed"):
        vm_create("pve", 150, "qemu", {key: "1"})
    assert fake.calls == []


@pytest.mark.parametrize("options,match", [("memory=2048", "options must be an object"),
                                           ({"Bad Key": 1}, "isn't a Proxmox parameter name"),
                                           ({"memory": [1]}, "must be text"),
                                           ({"name": "a\nb"}, "line break"),
                                           ({"hostname": "x"}, "ostemplate")])
def test_create_option_shapes(fake, options, match):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_create import vm_create
    with pytest.raises(ProxmoxError, match=match):
        vm_create("pve", 150, "lxc", options)


def test_queued_changes_are_audited(fake):
    import clients
    import proxmox_pending
    from tools.proxmox.vm_power import vm_power
    pid = vm_power(101, node="pve", action="shutdown", dry_run=False)["id"]
    proxmox_pending.decide(pid, approve=False)
    actions = [json.loads(line)["action"] for line in clients.AUDIT_FILE.read_text().splitlines()]
    assert actions == ["proxmox change waiting", "proxmox change declined"]


def test_admin_routes(fake, monkeypatch):
    """GET /proxmox/pending and POST /proxmox/pending/<id>/<approve|decline>, through the real route functions."""
    import asyncio

    from starlette.requests import Request

    import server
    from tools.proxmox.vm_power import vm_power

    def req(path_params):
        return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "path_params": path_params})

    pid = vm_power(101, node="pve", action="shutdown", dry_run=False)["id"]
    listed = json.loads(asyncio.run(server.proxmox_pending_list(req({}))).body)
    assert [i["id"] for i in listed["items"]] == [pid]
    bad = asyncio.run(server.proxmox_pending_decide(req({"item_id": pid, "action": "delete"})))
    assert bad.status_code == 400
    ok = asyncio.run(server.proxmox_pending_decide(req({"item_id": pid, "action": "approve"})))
    assert ok.status_code == 200 and json.loads(ok.body)["result"]["done"] is True
    gone = asyncio.run(server.proxmox_pending_decide(req({"item_id": pid, "action": "approve"})))
    assert gone.status_code == 404


def test_power_and_snapshot_find_the_node_cluster_wide(fake):
    import proxmox_pending
    from tools.proxmox.vm_power import vm_power
    from tools.proxmox.vm_snapshot import vm_snapshot
    r = vm_power(300, "shutdown")
    assert r["change"] == "shutdown qemu 300 on pve2"
    pid = vm_snapshot(300, "nightly", dry_run=False)["id"]
    assert proxmox_pending.list_pending()[0]["action"]["node"] == "pve2"
    proxmox_pending.decide(pid, approve=True)
    assert [p for _, p, _, _ in fake.posts()] == ["/nodes/pve2/qemu/300/snapshot"]


def test_a_wrong_node_given_is_refused(fake):
    from tools.proxmox._client import ProxmoxError
    from tools.proxmox.vm_power import vm_power
    with pytest.raises(ProxmoxError, match="is a qemu on pve2, not a qemu on pve"):
        vm_power(300, "start", node="pve")


def test_create_needs_a_node_and_can_target_any_cluster_node(fake):
    from tools.proxmox.vm_create import vm_create
    assert vm_create("pve2", 150, "qemu", {"name": "x"})["change"] == "create qemu 150 on pve2 with 1 option(s)"
