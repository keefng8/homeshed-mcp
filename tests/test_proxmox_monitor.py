"""The Proxmox monitor and its alarms (2026-10-09). Proxmox and the phone are faked; files go under tmp_path."""
import time

import pytest

import proxmox_monitor as pm
from tools.proxmox import _client as px

NOW = 1_800_000_000.0
TH = {"disk": 85.0, "node_ram": 90.0, "node_cpu": 95.0, "guest_ram": 98.0, "backup_days": 2.0, "wear": 20.0,
      "stopped": True}
GB = 1e9


def node(name="pve1", root_used=40 * GB, root_total=100 * GB, mem_used=8 * GB, status="online", disks=()):
    return {"node": name, "status": status, "cpu_pct": 5.0, "mem_used": mem_used, "mem_total": 32 * GB,
            "root_used": root_used, "root_total": root_total, "root_avail": root_total - root_used, "disks": list(disks)}


def guest(vmid=100, status="running", kind="qemu", **kw):
    return {"vmid": vmid, "name": f"vm{vmid}", "kind": kind, "node": "pve1", "status": status, "template": False,
            "mem": 2 * GB, "maxmem": 8 * GB, "maxdisk": 32 * GB, **kw}


def snap(nodes=None, guests=(), storage=(), tasks=(), quorate=True):
    return {"cluster": {"name": "c", "quorate": quorate}, "nodes": nodes if nodes is not None else [node()],
            "guests": list(guests), "storage": list(storage), "tasks": list(tasks)}


def run(state, s, th=TH, running=(), now=NOW):
    conds = pm.conditions(s, th, set(running), now, set(state["active"]))
    return pm.step(state, conds, now)


def fresh():
    return {"active": {}, "history": [], "counters": {}, "running": []}


def test_a_full_disk_fires_once_after_two_checks_and_clears_with_margin():
    st, full = fresh(), snap(nodes=[node(root_used=92 * GB)])
    assert run(st, full) == []                                # one check over the line: not yet
    [fired] = run(st, full, now=NOW + 60)
    assert fired["event"] == "fired" and fired["id"] == "node-disk:pve1" and fired["severity"] == "warning"
    assert "8.0 GB free of 100.0 GB" in fired["message"]
    assert run(st, full, now=NOW + 120) == []                 # still full: no second push
    assert run(st, snap(nodes=[node(root_used=84 * GB)]), now=NOW + 180) == []  # under, but inside the margin
    assert run(st, snap(nodes=[node(root_used=70 * GB)]), now=NOW + 240) == []  # one clear check
    [cleared] = run(st, snap(nodes=[node(root_used=70 * GB)]), now=NOW + 300)
    assert cleared["event"] == "cleared" and st["active"] == {} and st["history"][0]["id"] == "node-disk:pve1"
    assert st["counters"] == {}                               # nothing left over


def test_95_percent_is_urgent():
    st = fresh()
    run(st, snap(nodes=[node(root_used=96 * GB)]))
    [fired] = run(st, snap(nodes=[node(root_used=96 * GB)]), now=NOW + 60)
    assert fired["severity"] == "critical"


def test_off_disables_a_rule():
    conds = pm.conditions(snap(nodes=[node(root_used=99 * GB)]), {**TH, "disk": None}, set(), NOW)
    assert "node-disk:pve1" not in conds


def test_a_guest_that_stops_by_itself_alarms_until_it_runs_again():
    st = fresh()
    [fired] = run(st, snap(guests=[guest(status="stopped")]), running={100})
    assert fired["id"] == "stopped:100" and fired["severity"] == "critical"
    assert run(st, snap(guests=[guest(status="stopped")]), running=set(), now=NOW + 60) == []  # stays active
    assert "stopped:100" in st["active"]
    [cleared] = run(st, snap(guests=[guest(status="running")]), now=NOW + 120)
    assert cleared["event"] == "cleared"


def test_a_stop_somebody_asked_for_is_quiet():
    task = {"type": "qmshutdown", "id": "100", "node": "pve1", "start": NOW - 120, "status": "OK", "end": NOW - 100}
    assert run(fresh(), snap(guests=[guest(status="stopped")], tasks=[task]), running={100}) == []
    old = {**task, "start": NOW - 3600}  # a shutdown an hour ago doesn't explain a stop now
    assert run(fresh(), snap(guests=[guest(status="stopped")], tasks=[old]), running={100})[0]["id"] == "stopped:100"


def test_backups_too_old_or_failed_and_never_backed_up_is_quiet():
    st = fresh()
    s = snap(guests=[guest(100, last_backup=NOW - 3 * 86400), guest(101, last_backup=NOW - 3600), guest(102)])
    events = run(st, s)
    assert [e["id"] for e in events] == ["backup:100"] and "3 days" in events[0]["title"]
    failed = {"type": "vzdump", "node": "pve1", "status": "job errors", "start": NOW - 7200, "end": NOW - 7000}
    assert [e["id"] for e in run(fresh(), snap(tasks=[failed]))] == ["backup-failed:pve1"]
    ok_later = {**failed, "status": "OK", "start": NOW - 3600, "end": NOW - 3500}
    assert run(fresh(), snap(tasks=[failed, ok_later])) == []  # the newest backup worked


def test_storage_on_the_system_disk_is_the_nodes_alarm_and_unknown_storage_is_quiet():
    root_dir = {"storage": "local", "node": "pve1", "type": "dir", "status": "available", "used": 95 * GB,
                "total": 100 * GB}
    pool = {"storage": "tank", "node": None, "type": "zfspool", "status": "available", "used": 90 * GB, "total": 100 * GB}
    gone = {"storage": "nvme", "node": "pve1", "type": "lvmthin", "status": "unknown", "used": None, "total": None}
    conds = pm.conditions(snap(storage=[root_dir, pool, gone]), TH, set(), NOW)
    assert "storage:pve1:local" not in conds and conds["storage:shared:tank"]["over"]
    assert not any(k.startswith("storage:pve1:nvme") for k in conds)


def test_guest_disks_ram_and_physical_disks():
    g = guest(fs=[{"mount": "/", "used": 30 * GB, "total": 32 * GB}, {"mount": "/boot/efi", "used": 0.1 * GB,
                                                                      "total": 0.12 * GB}], mem=7.9 * GB)
    ct = guest(101, kind="lxc", disk=9 * GB, maxdisk=10 * GB)
    bad = {"dev": "/dev/sda", "model": "SSD", "health": "FAILED", "wear_left": 15}
    conds = pm.conditions(snap(nodes=[node(disks=[bad])], guests=[g, ct]), TH, set(), NOW)
    assert conds["guest-disk:100:/"]["over"] and "guest-disk:100:/boot/efi" not in conds  # tiny file systems: never
    assert conds["guest-disk:101:/"]["over"] and conds["guest-ram:100"]["over"]
    assert conds["guest-ram:100"]["sustain"] == 15
    assert conds["smart:pve1:/dev/sda"]["over"] and conds["smart:pve1:/dev/sda"]["severity"] == "critical"
    assert conds["wear:pve1:/dev/sda"]["over"]


def test_no_ram_alarm_on_a_host_side_figure():
    g = guest(mem=7.9 * GB, mem_basis="host", fs=[{"mount": "/", "used": 31 * GB, "total": 32 * GB}])
    conds = pm.conditions(snap(guests=[g]), TH, set(), NOW)
    assert "guest-ram:100" not in conds and conds["guest-disk:100:/"]["over"]  # disks still checked


def test_quorum_and_offline_nodes():
    conds = pm.conditions(snap(nodes=[node(status="offline")], quorate=False), TH, set(), NOW)
    assert conds["quorum"]["over"] and conds["node-offline:pve1"]["over"]
    assert "node-ram:pve1" not in conds  # no reading from an offline node: its other alarms keep their state


def test_an_alarm_whose_subject_is_gone_is_dropped_quietly():
    st = fresh()
    st["active"]["guest-ram:999"] = {"id": "guest-ram:999", "since": NOW - 2 * 86400, "seen": NOW - 2 * 86400}
    assert run(st, snap()) == [] and "guest-ram:999" not in st["active"]


# --- collect, check and history against a faked Proxmox -------------------------------------------------------------

@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "ALARMS_FILE", tmp_path / "alarms.json")
    monkeypatch.setattr(pm, "_snap", {"at": 0.0, "data": None})
    monkeypatch.setattr(pm, "_slow", {})
    monkeypatch.setattr(pm, "_agent", {})
    monkeypatch.setattr(pm, "_prev", {})
    monkeypatch.setattr(pm, "_own_meminfo", lambda: None)
    monkeypatch.setattr(pm, "_ips", {})
    monkeypatch.setattr(px, "config", lambda: {"base": "https://pve.test:8006"})
    pushes = []
    monkeypatch.setattr(pm, "_push", lambda e: pushes.append(e))
    monkeypatch.setattr(pm, "thresholds", lambda: dict(TH))
    data = {"down": False, "netin": 1000}

    def get(path, params=None):
        if data["down"]:
            raise px.ProxmoxError("could not reach Proxmox at https://pve.test:8006: ConnectError")
        if path == "/cluster/resources":
            return [{"type": "node", "node": "pve1", "status": "online", "cpu": 0.1, "maxcpu": 8, "mem": 1, "maxmem": 2,
                     "disk": 1, "maxdisk": 2, "uptime": 99},
                    {"type": "qemu", "vmid": 100, "name": "web", "node": "pve1", "status": "running", "cpu": 0.5,
                     "maxcpu": 2, "mem": 2 * GB, "maxmem": 4 * GB, "maxdisk": 32 * GB, "netin": data["netin"],
                     "netout": 0, "diskread": 0, "diskwrite": 0, "uptime": 50, "id": "qemu/100", "tags": "a;b"},
                    {"type": "qemu", "vmid": 101, "name": "db", "node": "pve1", "status": "running", "maxmem": 4 * GB,
                     "id": "qemu/101"},
                    {"type": "lxc", "vmid": 102, "name": "ct", "node": "pve1", "status": "running", "disk": 3 * GB,
                     "maxdisk": 8 * GB, "id": "lxc/102"},
                    {"type": "storage", "storage": "pbs", "node": "pve1", "shared": 1, "status": "available",
                     "content": "backup", "disk": 10 * GB, "maxdisk": 100 * GB, "plugintype": "pbs"}]
        if path == "/cluster/status":
            return [{"type": "cluster", "name": "c", "quorate": 1}, {"type": "node", "name": "pve1", "ip": "10.0.0.2"}]
        if path == "/cluster/tasks":
            return []
        if path == "/nodes/pve1/status":
            return {"memory": {"total": 32 * GB, "available": 24 * GB, "used": 30 * GB},
                    "rootfs": {"used": 20 * GB, "total": 100 * GB, "avail": 80 * GB}, "swap": {"used": 0, "total": GB},
                    "loadavg": ["0.5", "0.4", "0.3"], "pveversion": "pve-manager/9.2.11/abc",
                    "cpuinfo": {"model": "CPU", "cores": 4, "cpus": 8}}
        if path == "/nodes/pve1/disks/list":
            return [{"devpath": "/dev/nvme0n1", "model": "NVMe", "health": "PASSED", "wearout": 88, "size": 512 * GB}]
        if path == "/nodes/pve1/qemu/100/agent/get-fsinfo":
            return {"result": [{"mountpoint": "/", "type": "ext4", "used-bytes": 10 * GB, "total-bytes": 30 * GB}]}
        if path == "/nodes/pve1/qemu/101/agent/get-fsinfo":
            raise px.ProxmoxError("Proxmox answered HTTP 500 to GET ...: No QEMU guest agent configured")
        if path == "/nodes/pve1/qemu/100/agent/network-get-interfaces":
            return {"result": [{"name": "lo", "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "127.0.0.1"}]},
                               {"name": "docker0", "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "172.17.0.1"}]},
                               {"name": "br-1a2b", "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "172.18.0.1"}]},
                               {"name": "eth0", "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "10.0.0.50"},
                                                                 {"ip-address-type": "ipv6", "ip-address": "fe80::1"}]}]}
        if path == "/nodes/pve1/qemu/100/status/current":
            return {"ballooninfo": {"total_mem": 4 * GB, "free_mem": 2 * GB, "last_update": time.time() - 5}}
        if path == "/nodes/pve1/qemu/101/status/current":
            return {"ballooninfo": None}  # balloon device off
        if path == "/nodes/pve1/storage/pbs/content":
            return [{"vmid": 100, "ctime": NOW - 3600}, {"vmid": 100, "ctime": NOW - 90000}]
        if path.endswith("/rrddata"):
            return [{"time": 1, "cpu": 0.123456789, "mem": 5, "secret_field": 1}, {"cpu": 0.2}]
        raise AssertionError(f"unexpected {path}")
    monkeypatch.setattr(px, "get", get)
    return data, pushes


def test_collect_builds_the_tab_from_proxmox(fake):
    data, _ = fake
    s = pm.snapshot()
    [n] = s["nodes"]
    assert n["mem_used"] == 8 * GB and n["root_avail"] == 80 * GB and n["pve"] == "9.2.11" and n["load"] == [0.5, 0.4, 0.3]
    assert n["disks"][0]["wear_left"] == 88 and n["ip"] == "10.0.0.2"
    web, db, ct = s["guests"]
    assert web["fs"][0]["mount"] == "/" and web["last_backup"] == NOW - 3600 and web["tags"] == ["a", "b"]
    assert db.get("fs") is None and db["fs_note"] == "no guest agent" and ct["disk"] == 3 * GB
    assert web["mem_basis"] == "guest" and db["mem_basis"] == "host" and "mem_basis" not in ct
    assert web["ips"] == ["10.0.0.50"] and "ips" not in db  # no agent: not asked
    assert s["storage"][0]["shared"] and s["cluster"] == {"name": "c", "quorate": True, "nodes_online": 1, "nodes": 1}
    assert s["alarms"] == {"active": [], "recent": [], "enabled": True}
    data["netin"] = 61000
    pm._snap["at"] = 0
    pm._prev["qemu/100"] = (time.time() - 60, 1000.0, 0.0, 0.0, 0.0)
    web2 = pm.snapshot()["guests"][0]
    assert 900 <= web2["net_in_bps"] <= 1100  # 60,000 bytes in about 60 s
    pm._own_meminfo = lambda: (4 * GB, 3 * GB)  # this server runs inside VM 100 (same MemTotal): its real use
    pm._snap["at"] = 0
    web = pm.snapshot()["guests"][0]
    assert web["mem_basis"] == "available" and web["mem"] == 1 * GB


def test_own_meminfo_reads_total_and_available(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal:       16312304 kB\nMemFree:  7345584 kB\nMemAvailable:   10926732 kB\n")
    assert pm._own_meminfo(str(p)) == (16312304 * 1024, 10926732 * 1024)
    assert pm._own_meminfo(str(tmp_path / "missing")) is None


def test_check_saves_state_and_an_unreachable_api_alarms_after_three_tries(fake):
    data, pushes = fake
    assert pm.check(NOW) == [] and pm._load()["running"] == [100, 101, 102]
    data["down"] = True
    pm.check(NOW + 60)
    pm.check(NOW + 120)
    [fired] = pm.check(NOW + 180)
    assert fired["id"] == "unreachable" and pushes[-1]["event"] == "fired"
    assert pm._load()["active"]["unreachable"]["severity"] == "critical"
    data["down"] = False
    pm.check(NOW + 240)
    assert pm.check(NOW + 300)[0]["event"] == "cleared"


def test_history_keeps_only_graph_fields_and_checks_input(fake):
    h = pm.history("qemu", "pve1", 100, "day")
    assert h["points"] == [{"time": 1, "cpu": 0.1235, "mem": 5}]
    with pytest.raises(px.ProxmoxError):
        pm.history("node", "pve1", None, "decade")
    with pytest.raises(px.ProxmoxError):
        pm.history("qemu", "../x", 100)


def test_health_tool_sums_up(fake):
    from tools.proxmox.health import health
    out = health()
    assert out["nodes"][0]["ram_pct"] == 25.0 and out["guests"] == {"running": 3, "stopped": 0, "templates": 0}
    assert out["storage"][0]["node"] == "shared" and out["alarms"] == []


def test_name_lists_accept_commas_and_empty():
    import runtime_settings as rs
    assert rs.validate("approvals_trusted_clients", " shop-app , other ") == "shop-app,other"
    assert rs.validate("approvals_trusted_auto_clients", "") == ""
    with pytest.raises(ValueError):
        rs.validate("approvals_trusted_clients", "shop-app other")
    assert rs.SCHEMA["proxmox_alarm_disk"]["default"] == "85"
