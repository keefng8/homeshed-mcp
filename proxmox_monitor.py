"""Proxmox monitor: real disk space, RAM and CPU for every node, VM and container in a Proxmox cluster, with graphs and
phone alarms.

snapshot(): the whole cluster in one answer for the Control Panel's Proxmox tab (/proxmox/monitor): each node's CPU, RAM,
swap, root disk, load and physical disks (SMART health, wear), the storage pools, and every VM and container (CPU, RAM,
disk use, network and disk speed, last backup), with the alarms. history(): Proxmox's own graphs (rrddata) for one node
or guest. Read-only: nothing here changes anything on Proxmox.

Disk use inside a VM comes from its QEMU guest agent (get-fsinfo); without one Proxmox only knows the disk's size, and
the tab says so. A container's disk use comes straight from Proxmox.

Alarms (a check every CHECK_S in the background, started by the server; Settings > Alerts > Proxmox alarms): a rule over
its line for its number of checks in a row fires ONE phone push and stays active until it's back under (by MARGIN
points) for CLEAR_AFTER checks, then one "back to normal" push. Active alarms, their last week's history and which
guests were running are kept in ALARMS_FILE, so a restart doesn't push them again. The rules:
- the cluster losing quorum, a node going offline, or Proxmox's API not answering;
- a node's RAM, CPU (sustained) or root disk, a storage pool, or a disk inside a guest over its line;
- a guest's RAM over its line for a while (a VM's RAM includes its cache, so this line is high); a VM with no in-guest
  figure (balloon device off: mem_basis "host") gets none, as its number is the node's view, not the guest's;
- a running guest that stops with no stop, shutdown or migrate task for it in the last STOP_TASK_S (a crash, or it
  powered itself off), until it runs again;
- a guest that has backups, but none newer than the backup age; a failed backup task in the last day;
- a physical disk whose SMART health isn't PASSED/OK, or whose remaining life (Proxmox's "wearout") is low.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from paths import data_path

ALARMS_FILE = Path(os.environ.get("PROXMOX_ALARMS_FILE") or data_path("usage/proxmox_alarms.json"))
CHECK_S = 60
FIRST_S = 30           # the first check after the server starts
FRESH_S = 20           # a snapshot younger than this is served as it is
SLOW_S = 1800          # physical disks and backups change slowly
AGENT_S = 300          # a VM's file systems, from its guest agent
NO_AGENT_S = 1800      # a VM with no guest agent configured is asked again after this
BALLOON_STALE_S = 300  # in-guest RAM stats older than this don't count (a VM just booted, a stuck driver)
MARGIN = 3.0
CLEAR_AFTER = 2
STOP_TASK_S = 900
UNREACHABLE_AFTER = 3
KEEP_S = 7 * 86400
MIN_FS_BYTES = 1 << 30  # file systems smaller than 1 GiB (EFI, boot) never alarm
TIMEFRAMES = ("hour", "day", "week", "month", "year")
STOP_TASKS = {"qmstop", "qmshutdown", "qmreboot", "qmmigrate", "qmrollback", "qmdestroy", "qmsuspend", "vzstop",
              "vzshutdown", "vzreboot", "vzmigrate", "vzrollback", "vzdestroy", "vzsuspend"}
NODE_POINT = ("time", "cpu", "memused", "memtotal", "memavailable", "swapused", "swaptotal", "rootused", "roottotal",
              "netin", "netout", "iowait", "loadavg")
GUEST_POINT = ("time", "cpu", "mem", "maxmem", "netin", "netout", "diskread", "diskwrite", "disk", "maxdisk")

SETTINGS = {
    "proxmox_alarms_enabled": {
        "type": "bool", "default": True, "group": "Proxmox alarms",
        "label": "Proxmox alarms on your phone",
        "help": "Checks the cluster every minute and pushes one alert when something crosses a line below, and one "
                "when it's back to normal. The Proxmox tab shows the same alarms.",
    },
    "proxmox_alarm_disk": {
        "type": "select", "default": "85", "group": "Proxmox alarms", "label": "Disk full alarm",
        "options": ["off", "75", "80", "85", "90", "95"],
        "option_labels": {"off": "Off", **{v: f"At {v}% full" for v in ("75", "80", "85", "90", "95")}},
        "help": "A node's system disk, a storage pool, or a disk inside a VM or container (95% and over is urgent).",
    },
    "proxmox_alarm_node_ram": {
        "type": "select", "default": "90", "group": "Proxmox alarms", "label": "Node RAM alarm",
        "options": ["off", "80", "85", "90", "95"],
        "option_labels": {"off": "Off", **{v: f"At {v}% used" for v in ("80", "85", "90", "95")}},
        "help": "A node's memory in use (not counting what it can free), for 3 minutes in a row.",
    },
    "proxmox_alarm_node_cpu": {
        "type": "select", "default": "95", "group": "Proxmox alarms", "label": "Node CPU alarm",
        "options": ["off", "80", "90", "95"],
        "option_labels": {"off": "Off", **{v: f"At {v}% for 10 minutes" for v in ("80", "90", "95")}},
        "help": "A node's CPU busy for 10 minutes in a row.",
    },
    "proxmox_alarm_guest_ram": {
        "type": "select", "default": "98", "group": "Proxmox alarms", "label": "VM and container RAM alarm",
        "options": ["off", "90", "95", "98"],
        "option_labels": {"off": "Off", **{v: f"At {v}% for 15 minutes" for v in ("90", "95", "98")}},
        "help": "A VM's RAM includes its file cache, so it often sits high; this alarm waits 15 minutes.",
    },
    "proxmox_alarm_backup_days": {
        "type": "select", "default": "2", "group": "Proxmox alarms", "label": "Backup too old alarm",
        "options": ["off", "1", "2", "3", "7"],
        "option_labels": {"off": "Off", "1": "Older than 1 day", "2": "Older than 2 days", "3": "Older than 3 days",
                          "7": "Older than a week"},
        "help": "A VM or container that has backups, but none newer than this. A failed backup always alarms.",
    },
    "proxmox_alarm_guest_stopped": {
        "type": "bool", "default": True, "group": "Proxmox alarms", "label": "VM or container stopped by itself",
        "help": "A running VM or container that stops without anyone stopping, shutting down or moving it (a crash, "
                "or it powered itself off). Urgent.",
    },
    "proxmox_alarm_disk_wear": {
        "type": "select", "default": "20", "group": "Proxmox alarms", "label": "Physical disk wear alarm",
        "options": ["off", "10", "20", "30"],
        "option_labels": {"off": "Off", **{v: f"At {v}% life left" for v in ("10", "20", "30")}},
        "help": "An SSD's remaining life, from its SMART data. A failing SMART health check always alarms.",
    },
}

_lock = threading.Lock()
_state_lock = threading.Lock()
_snap: dict = {"at": 0.0, "data": None}
_slow: dict = {}       # key -> (at, value): physical disks per node, backups
_agent: dict = {}      # vmid -> (at, file systems | None, why)
_prev: dict = {}       # guest id -> (at, netin, netout, diskread, diskwrite) for speeds
_started = False


# --- reading Proxmox -------------------------------------------------------------------------------------------------

def _px():
    from tools.proxmox import _client as px
    return px


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(used, total):
    u, t = _num(used), _num(total)
    return round(u * 100 / t, 1) if u is not None and t else None


def _cached(key: str, max_age: float, fetch):
    hit = _slow.get(key)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    value = fetch()
    _slow[key] = (time.time(), value)
    return value


def _node_detail(name: str) -> dict:
    px = _px()
    try:
        st = px.get(f"/nodes/{px.check_node(name)}/status") or {}
    except px.ProxmoxError as exc:
        return {"error": str(exc)[:200]}
    mem, swap, root = st.get("memory") or {}, st.get("swap") or {}, st.get("rootfs") or {}
    cpu = st.get("cpuinfo") or {}
    avail = _num(mem.get("available"))
    used = (_num(mem.get("total")) - avail) if avail is not None and _num(mem.get("total")) else _num(mem.get("used"))
    return {"mem_used": used, "mem_total": _num(mem.get("total")), "swap_used": _num(swap.get("used")),
            "swap_total": _num(swap.get("total")), "root_used": _num(root.get("used")),
            "root_total": _num(root.get("total")), "root_avail": _num(root.get("avail")),
            "load": [_num(x) for x in (st.get("loadavg") or [])][:3], "iowait": _num(st.get("wait")),
            "cpu_model": str(cpu.get("model") or "")[:80], "cores": cpu.get("cores"), "threads": cpu.get("cpus"),
            "pve": str(st.get("pveversion") or "").split("/")[1] if "/" in str(st.get("pveversion") or "") else None,
            "kernel": str((st.get("current-kernel") or {}).get("release") or "")[:60] or None}


def _phys_disks(name: str) -> list[dict]:
    px = _px()

    def fetch():
        try:
            rows = px.get(f"/nodes/{px.check_node(name)}/disks/list") or []
        except px.ProxmoxError:
            return []
        return [{"dev": r.get("devpath"), "model": str(r.get("model") or "")[:60], "type": r.get("type"),
                 "size": _num(r.get("size")), "health": r.get("health"),
                 "wear_left": r.get("wearout") if isinstance(r.get("wearout"), (int, float)) else None}
                for r in rows if isinstance(r, dict)]
    return _cached(f"disks:{name}", SLOW_S, fetch)


def _guest_fs(node: str, vmid: int) -> tuple[list[dict] | None, str]:
    """A running VM's file systems from its guest agent: (file systems, "") or (None, why not)."""
    hit = _agent.get(vmid)
    if hit and time.time() - hit[0] < (NO_AGENT_S if hit[2] == "no guest agent" else AGENT_S):
        return hit[1], hit[2]
    px = _px()
    try:
        d = px.get(f"/nodes/{px.check_node(node)}/qemu/{px.check_vmid(vmid)}/agent/get-fsinfo") or {}
        fs = [{"mount": str(f.get("mountpoint") or f.get("name") or "")[:120], "type": f.get("type"),
               "used": _num(f.get("used-bytes")), "total": _num(f.get("total-bytes"))}
              for f in (d.get("result") or []) if isinstance(f, dict) and _num(f.get("total-bytes"))]
        out = (sorted(fs, key=lambda f: -(f["total"] or 0)), "")
    except px.ProxmoxError as exc:
        why = "no guest agent" if "No QEMU guest agent configured" in str(exc) else "guest agent not answering"
        out = (None, why)
    _agent[vmid] = (time.time(), *out)
    return out


_ips: dict[int, tuple[float, list[str]]] = {}
# Docker's and bridges' own interfaces: their addresses aren't the VM's (a Docker server lists many)
VIRTUAL_IFACES = ("lo", "docker", "br-", "veth", "virbr", "cni", "flannel", "tailscale", "wg")


def _guest_ips(node: str, vmid: int) -> list[str]:
    """A running VM's IPv4 addresses from its guest agent (network-get-interfaces), loopback left out; [] without an
    agent. The Control Panel uses them to find the VM among its saved machines and read its real memory use."""
    hit = _ips.get(vmid)
    if hit and time.time() - hit[0] < AGENT_S:
        return hit[1]
    px = _px()
    try:
        d = px.get(f"/nodes/{px.check_node(node)}/qemu/{px.check_vmid(vmid)}/agent/network-get-interfaces") or {}
        out = sorted({str(a.get("ip-address")) for i in d.get("result") or [] if isinstance(i, dict)
                      and not str(i.get("name") or "").startswith(VIRTUAL_IFACES)
                      for a in i.get("ip-addresses") or [] if isinstance(a, dict)
                      and a.get("ip-address-type") == "ipv4" and not str(a.get("ip-address")).startswith("127.")})[:8]
    except px.ProxmoxError:
        out = []
    _ips[vmid] = (time.time(), out)
    return out


def _mem_basis(node: str, vmid: int) -> tuple[str, float | None]:
    """Where a running VM's RAM figure comes from, and the guest's own MemTotal: ("guest", total) when its balloon
    driver reports fresh in-guest stats (Linux MemTotal - MemFree, so the page cache counts as used: Proxmox doesn't pass
    on MemAvailable), else ("host", None) (the QEMU process's RAM on the node, which keeps what the guest once touched;
    checked 2026-10-10 on VM 120, balloon off)."""
    px = _px()
    try:
        b = (px.get(f"/nodes/{px.check_node(node)}/qemu/{px.check_vmid(vmid)}/status/current") or {}).get("ballooninfo")
    except px.ProxmoxError:
        return "host", None
    fresh = isinstance(b, dict) and b.get("total_mem") and b.get("free_mem") is not None and \
        time.time() - (_num(b.get("last_update")) or 0) < BALLOON_STALE_S
    return ("guest", _num(b.get("total_mem"))) if fresh else ("host", None)


def _own_meminfo(path: str = "/proc/meminfo") -> tuple[float, float] | None:
    """(MemTotal, MemAvailable) in bytes of the machine this server runs on: a container sees its VM's /proc/meminfo,
    so this is the real figure for the VM hosting the tool server (R&D, 2026-10-10)."""
    try:
        f = {k: float(v.split()[0]) * 1024 for k, v in
             (line.split(":", 1) for line in Path(path).read_text().splitlines() if ":" in line)}
        return f["MemTotal"], f["MemAvailable"]
    except (OSError, KeyError, ValueError):
        return None


def _backups(resources: list[dict]) -> dict[int, float]:
    """The newest backup time of every guest, from every storage that holds backups (a shared one asked once)."""
    px = _px()

    def fetch():
        newest: dict[int, float] = {}
        seen_shared = set()
        for s in resources:
            if s.get("type") != "storage" or s.get("status") != "available" or "backup" not in str(s.get("content")):
                continue
            if s.get("shared"):
                if s.get("storage") in seen_shared:
                    continue
                seen_shared.add(s.get("storage"))
            try:
                rows = px.get(f"/nodes/{px.check_node(s.get('node'))}/storage/{s.get('storage')}/content",
                              {"content": "backup"}) or []
            except px.ProxmoxError:
                continue
            for b in rows:
                vmid, ct = b.get("vmid"), _num(b.get("ctime"))
                if isinstance(vmid, int) and ct and ct > newest.get(vmid, 0):
                    newest[vmid] = ct
        return newest
    return _cached("backups", SLOW_S, fetch)


def _speeds(gid: str, r: dict, now: float) -> dict:
    cur = (now, *(_num(r.get(k)) or 0.0 for k in ("netin", "netout", "diskread", "diskwrite")))
    prev, _prev[gid] = _prev.get(gid), cur
    if not prev or cur[0] - prev[0] <= 0 or r.get("status") != "running":
        return {}
    dt = cur[0] - prev[0]
    keys = ("net_in_bps", "net_out_bps", "disk_read_bps", "disk_write_bps")
    return {k: max(0.0, round((c - p) / dt, 1)) for k, c, p in zip(keys, cur[1:], prev[1:])}


def collect() -> dict:
    """One fresh look at the whole cluster (see the module notes). Raises ProxmoxError when Proxmox can't be read."""
    px = _px()
    now = time.time()
    resources = [r for r in px.get("/cluster/resources") or [] if isinstance(r, dict)]
    try:
        status = [r for r in px.get("/cluster/status") or [] if isinstance(r, dict)]
    except px.ProxmoxError:
        status = []
    try:
        tasks = [t for t in px.get("/cluster/tasks") or [] if isinstance(t, dict)]
    except px.ProxmoxError:
        tasks = []
    cl = next((r for r in status if r.get("type") == "cluster"), None)
    ips = {r.get("name"): r.get("ip") for r in status if r.get("type") == "node"}
    node_rows = sorted((r for r in resources if r.get("type") == "node"), key=lambda r: str(r.get("node")))
    online = [r["node"] for r in node_rows if r.get("status") == "online"]
    running_qemu = [r for r in resources if r.get("type") == "qemu" and r.get("status") == "running" and not r.get("template")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        details = dict(zip(online, pool.map(_node_detail, online)))
        disks = dict(zip(online, pool.map(_phys_disks, online)))
        fs = dict(zip([r["vmid"] for r in running_qemu],
                      pool.map(lambda r: _guest_fs(r["node"], r["vmid"]), running_qemu)))
        basis = dict(zip([r["vmid"] for r in running_qemu],
                         pool.map(lambda r: _mem_basis(r["node"], r["vmid"]), running_qemu)))
        with_agent = [r for r in running_qemu if (fs.get(r["vmid"]) or (None, ""))[1] != "no guest agent"]
        guest_ips = dict(zip([r["vmid"] for r in with_agent],
                             pool.map(lambda r: _guest_ips(r["node"], r["vmid"]), with_agent)))
    backups = _backups(resources)
    own, totals = _own_meminfo(), [t for _, t in basis.values() if t]
    nodes = []
    for r in node_rows:
        n = r.get("node")
        d = details.get(n) or {}
        nodes.append({"node": n, "status": r.get("status"), "ip": ips.get(n), "cpu_pct": _pct(r.get("cpu"), 1),
                      "cpus": r.get("maxcpu"), "mem_used": d.get("mem_used", _num(r.get("mem"))),
                      "mem_total": d.get("mem_total", _num(r.get("maxmem"))),
                      "root_used": d.get("root_used", _num(r.get("disk"))),
                      "root_total": d.get("root_total", _num(r.get("maxdisk"))), "root_avail": d.get("root_avail"),
                      "uptime_s": r.get("uptime"), "disks": disks.get(n, []),
                      **{k: d.get(k) for k in ("swap_used", "swap_total", "load", "iowait", "cpu_model", "cores",
                                                "threads", "pve", "kernel", "error")}})
    storage, shared_seen = [], {}
    for s in sorted((r for r in resources if r.get("type") == "storage"), key=lambda r: (not r.get("shared"),
                                                                                          str(r.get("node")),
                                                                                          str(r.get("storage")))):
        if s.get("shared"):
            if s.get("storage") in shared_seen:
                shared_seen[s.get("storage")]["nodes"].append(s.get("node"))
                continue
        row = {"storage": s.get("storage"), "node": None if s.get("shared") else s.get("node"),
               "nodes": [s.get("node")], "type": s.get("plugintype"), "shared": bool(s.get("shared")),
               "status": s.get("status"), "content": s.get("content"), "used": _num(s.get("disk")),
               "total": _num(s.get("maxdisk"))}
        if s.get("shared"):
            shared_seen[s.get("storage")] = row
        storage.append(row)
    guests = []
    for r in sorted((r for r in resources if r.get("type") in ("qemu", "lxc")), key=lambda r: r.get("vmid") or 0):
        vmid, kind = r.get("vmid"), r.get("type")
        g = {"vmid": vmid, "name": r.get("name"), "kind": kind, "node": r.get("node"), "status": r.get("status"),
             "template": bool(r.get("template")), "cpus": r.get("maxcpu"), "cpu_pct": _pct(r.get("cpu"), 1),
             "mem": _num(r.get("mem")), "maxmem": _num(r.get("maxmem")), "maxdisk": _num(r.get("maxdisk")),
             "uptime_s": r.get("uptime"), "tags": [t for t in str(r.get("tags") or "").split(";") if t][:8],
             "last_backup": backups.get(vmid), **_speeds(str(r.get("id")), r, now)}
        if kind == "lxc":
            g["disk"] = _num(r.get("disk")) if r.get("status") == "running" else None
        if guest_ips.get(vmid):
            g["ips"] = guest_ips[vmid]
        if vmid in basis:
            g["mem_basis"], total = basis[vmid]
            if own and total == own[0] and totals.count(total) == 1:  # the VM this server runs in: its real use
                g["mem_basis"], g["mem"] = "available", own[0] - own[1]
        if vmid in fs:
            g["fs"], why = fs[vmid]
            if why:
                g["fs_note"] = why
        guests.append(g)
    return {"at": now, "host": px.config().get("base"),
            "cluster": {"name": (cl or {}).get("name"), "quorate": bool(cl.get("quorate")) if cl else None,
                        "nodes_online": len(online), "nodes": len(node_rows)},
            "nodes": nodes, "storage": storage, "guests": guests,
            "tasks": [{"type": t.get("type"), "id": t.get("id"), "node": t.get("node"), "status": t.get("status"),
                       "start": t.get("starttime"), "end": t.get("endtime"), "user": t.get("user")}
                      for t in tasks[:200]]}


def _fresh(max_age: float) -> dict:
    """The latest whole look, no older than max_age seconds (shared by the tab and the alarm checks)."""
    with _lock:
        if _snap["data"] is None or time.time() - _snap["at"] >= max_age:
            _snap["data"], _snap["at"] = collect(), time.time()
        return _snap["data"]


def snapshot(max_age: float = FRESH_S) -> dict:
    """The latest look (no older than max_age seconds) with the alarms, for the Proxmox tab."""
    data = dict(_fresh(max_age))
    state = _load()
    data["tasks"] = [t for t in data.get("tasks") or [] if t.get("status") not in (None, "OK")
                     or t.get("type") == "vzdump"][:30]  # the tab shows failures and backups only
    data["alarms"] = {"active": sorted(state["active"].values(), key=lambda a: (a.get("severity") != "critical",
                                                                                -a.get("since", 0))),
                      "recent": state["history"][:30], "enabled": _setting("proxmox_alarms_enabled") is not False}
    return data


def history(kind: str, node: str, vmid: int | None = None, timeframe: str = "hour") -> dict:
    """Proxmox's own graph points for a node (kind "node") or a guest (qemu / lxc), averaged per step."""
    px = _px()
    node = px.check_node(node)
    if timeframe not in TIMEFRAMES:
        raise px.ProxmoxError(f"timeframe must be one of {list(TIMEFRAMES)}")
    if kind == "node":
        path, keep = f"/nodes/{node}/rrddata", NODE_POINT
    else:
        path, keep = f"/nodes/{node}/{px.check_kind(kind)}/{px.check_vmid(vmid)}/rrddata", GUEST_POINT
    rows = px.get(path, {"timeframe": timeframe, "cf": "AVERAGE"}) or []
    points = [{k: (round(v, 4) if isinstance(v, float) else v) for k in keep if (v := r.get(k)) is not None}
              for r in rows if isinstance(r, dict) and r.get("time")]
    return {"kind": kind, "node": node, "vmid": vmid, "timeframe": timeframe, "points": points}


# --- alarms ----------------------------------------------------------------------------------------------------------

def _setting(key: str):
    try:
        import runtime_settings
        return runtime_settings.get(key)
    except Exception:  # noqa: BLE001 - no settings readable: the default
        return SETTINGS[key]["default"]


def thresholds() -> dict:
    def num(key):
        v = _setting(key)
        return None if v in (None, "off") else float(v)
    return {"disk": num("proxmox_alarm_disk"), "node_ram": num("proxmox_alarm_node_ram"),
            "node_cpu": num("proxmox_alarm_node_cpu"), "guest_ram": num("proxmox_alarm_guest_ram"),
            "backup_days": num("proxmox_alarm_backup_days"), "wear": num("proxmox_alarm_disk_wear"),
            "stopped": _setting("proxmox_alarm_guest_stopped") is not False}


def _gb(n) -> str:
    return f"{(n or 0) / 1e9:.1f} GB"


def conditions(snap: dict, th: dict, running_before: set, now: float | None = None,
               active: set | frozenset = frozenset()) -> dict:
    """Every rule's verdict on one look: {alarm id: {"over", "value", "severity", "title", "message", "sustain",
    "under"}}. "under" is true only when the value is clear of the line by MARGIN (so it can count towards clearing);
    a rule with no reading this time (a node offline, an agent not answering) is left out and keeps its state.
    running_before: the guests running at the last check; active: the alarm ids active now."""
    now = now or time.time()
    out: dict = {}

    def add(aid, over, *, value=None, severity="warning", title="", message="", sustain=2, under=None):
        out[aid] = {"over": bool(over), "under": (not over) if under is None else bool(under), "value": value,
                    "severity": severity, "title": title, "message": message, "sustain": sustain}

    def level(aid, pct, line, sustain, title, message, urgent_at=None):
        if line is None or pct is None:
            return
        add(aid, pct >= line, value=pct, sustain=sustain, under=pct < line - MARGIN, title=title, message=message,
            severity="critical" if urgent_at is not None and pct >= urgent_at else "warning")

    cl = snap.get("cluster") or {}
    if cl.get("quorate") is not None:
        add("quorum", not cl["quorate"], severity="critical", title=f"Cluster {cl.get('name') or ''} lost quorum",
            message="Too few nodes can see each other: VMs keep running, but nothing can be started, changed or "
                    "backed up until enough nodes are back.")
    root_totals = {}
    for n in snap.get("nodes") or []:
        name = n.get("node")
        add(f"node-offline:{name}", n.get("status") != "online", severity="critical", title=f"Node {name} is offline",
            message=f"Proxmox can't reach {name}; its VMs and containers may be down.")
        if n.get("status") != "online":
            continue
        root_totals[name] = n.get("root_total")
        ram = _pct(n.get("mem_used"), n.get("mem_total"))
        level(f"node-ram:{name}", ram, th.get("node_ram"), 3, f"{name} RAM {ram:.0f}% used" if ram else "",
              f"{name} is using {_gb(n.get('mem_used'))} of {_gb(n.get('mem_total'))} of RAM.")
        level(f"node-cpu:{name}", n.get("cpu_pct"), th.get("node_cpu"), 10, f"{name} CPU busy",
              f"{name}'s CPU has been at {n.get('cpu_pct') or 0:.0f}% or more for 10 minutes.")
        disk = _pct(n.get("root_used"), n.get("root_total"))
        free = n.get("root_avail")
        free = (n.get("root_total") or 0) - (n.get("root_used") or 0) if free is None else free
        level(f"node-disk:{name}", disk, th.get("disk"), 2, f"{name} system disk {disk:.0f}% full" if disk else "",
              f"{name}'s system disk has {_gb(free)} free of {_gb(n.get('root_total'))}. A full system disk can stop "
              "the node's cluster services and backups.", urgent_at=95)
        for d in n.get("disks") or []:
            dev = d.get("dev")
            health = str(d.get("health") or "").upper()
            if health:
                add(f"smart:{name}:{dev}", health not in ("PASSED", "OK", "UNKNOWN"), severity="critical",
                    title=f"{name} disk {dev} SMART: {d.get('health')}",
                    message=f"{d.get('model') or dev} on {name} reports SMART health {d.get('health')}. Back up what's "
                            "on it and plan to replace it.")
            left = d.get("wear_left")
            if th.get("wear") is not None and isinstance(left, (int, float)):
                add(f"wear:{name}:{dev}", left <= th["wear"], value=left, under=left > th["wear"] + MARGIN,
                    title=f"{name} disk {dev}: {left:.0f}% life left",
                    message=f"{d.get('model') or dev} on {name} has {left:.0f}% of its rated life left.")
    for s in snap.get("storage") or []:
        if s.get("status") != "available" or not s.get("total"):
            continue
        where = s.get("node") or "shared"
        if s.get("type") == "dir" and s.get("node") and root_totals.get(s["node"]) and \
                abs((s["total"] or 0) - root_totals[s["node"]]) <= 0.01 * root_totals[s["node"]]:
            continue  # a directory on the node's system disk: the node's own disk alarm covers it
        pct = _pct(s.get("used"), s.get("total"))
        level(f"storage:{where}:{s.get('storage')}", pct, th.get("disk"), 2,
              f"Storage {s.get('storage')} ({where}) {pct:.0f}% full" if pct else "",
              f"{s.get('storage')} on {where} has {_gb((s.get('total') or 0) - (s.get('used') or 0))} free of "
              f"{_gb(s.get('total'))}.", urgent_at=95)
    recent_stop = {str(t.get("id")) for t in snap.get("tasks") or []
                   if t.get("type") in STOP_TASKS and (t.get("start") or 0) >= now - STOP_TASK_S}
    for g in snap.get("guests") or []:
        if g.get("template"):
            continue
        vmid, label = g.get("vmid"), f"{g.get('name') or g.get('vmid')} ({g.get('vmid')})"
        running = g.get("status") == "running"
        sid = f"stopped:{vmid}"
        if th.get("stopped"):
            # over while it stays stopped once alarmed; fires when a guest that was running stops with no task for it
            over = not running and (sid in active or (vmid in running_before and str(vmid) not in recent_stop))
            if over or running:
                add(sid, over, severity="critical", sustain=1, title=f"{label} stopped by itself",
                    message=f"{label} on {g.get('node')} was running and stopped without a stop or shutdown task: a "
                            "crash, or it powered itself off.")
        if running:
            if g.get("mem_basis") != "host":  # a host-side figure isn't what the guest uses: no RAM alarm on it
                ram = _pct(g.get("mem"), g.get("maxmem"))
                level(f"guest-ram:{vmid}", ram, th.get("guest_ram"), 15,
                      f"{label} RAM {ram:.0f}% used" if ram else "",
                      f"{label} has used {ram or 0:.0f}% of its {_gb(g.get('maxmem'))} RAM for 15 minutes.")
            if g.get("kind") == "lxc" and g.get("disk") is not None:
                pct = _pct(g.get("disk"), g.get("maxdisk"))
                level(f"guest-disk:{vmid}:/", pct, th.get("disk"), 2, f"{label} disk {pct:.0f}% full" if pct else "",
                      f"{label} has {_gb((g.get('maxdisk') or 0) - (g.get('disk') or 0))} free of "
                      f"{_gb(g.get('maxdisk'))}.", urgent_at=95)
            for f in g.get("fs") or []:
                if (f.get("total") or 0) < MIN_FS_BYTES:
                    continue
                pct = _pct(f.get("used"), f.get("total"))
                level(f"guest-disk:{vmid}:{f.get('mount')}", pct, th.get("disk"), 2,
                      f"{label} {f.get('mount')} {pct:.0f}% full" if pct else "",
                      f"{label}'s {f.get('mount')} has {_gb((f.get('total') or 0) - (f.get('used') or 0))} free of "
                      f"{_gb(f.get('total'))}.", urgent_at=95)
        last = g.get("last_backup")
        if th.get("backup_days") is not None and last:
            age_d = (now - last) / 86400
            add(f"backup:{vmid}", age_d > th["backup_days"], value=round(age_d, 1), sustain=1,
                title=f"{label}: no backup for {age_d:.0f} days" if age_d >= 1.5 else f"{label}: backup overdue",
                message=f"{label}'s newest backup is {age_d:.1f} days old (alarm after {th['backup_days']:.0f}).")
    latest: dict = {}  # each node's newest finished backup task of the last day decides
    for t in snap.get("tasks") or []:
        if t.get("type") == "vzdump" and t.get("end") and (t.get("end") or 0) >= now - 86400:
            if (t.get("start") or 0) > (latest.get(t.get("node"), {}).get("start") or 0):
                latest[t.get("node")] = t
    for name in {n.get("node") for n in snap.get("nodes") or []}:
        t = latest.get(name)
        bad = bool(t) and t.get("status") != "OK"
        add(f"backup-failed:{name}", bad, sustain=1, title=f"A backup on {name} failed",
            message=f"The backup task on {name} ended with: {str((t or {}).get('status'))[:160]}. Check Proxmox's task "
                    "log for that night.")
    return out


def step(state: dict, conds: dict, now: float | None = None) -> list[dict]:
    """Move each alarm on by one check; returns the events to push: {"event": "fired"|"cleared", **alarm}."""
    now = now or time.time()
    events = []
    counters = state.setdefault("counters", {})
    for aid, c in conds.items():
        cnt = counters.get(aid) or {"over": 0, "under": 0}
        active = state["active"].get(aid)
        if c["over"]:
            cnt = {"over": cnt["over"] + 1, "under": 0}
            if active:
                active.update(value=c["value"], seen=now, title=c["title"] or active.get("title"),
                              severity="critical" if c["severity"] == "critical" else active.get("severity"))
            elif cnt["over"] >= c["sustain"]:
                alarm = {"id": aid, "severity": c["severity"], "title": c["title"], "message": c["message"],
                         "value": c["value"], "since": now, "seen": now}
                state["active"][aid] = alarm
                events.append({"event": "fired", **alarm})
        else:
            cnt = {"over": 0, "under": cnt["under"] + 1 if c["under"] else 0}
            if active:
                active["seen"] = now
                if cnt["under"] >= (1 if c["sustain"] == 1 else CLEAR_AFTER):
                    del state["active"][aid]
                    state["history"].insert(0, {**active, "cleared": now})
                    events.append({"event": "cleared", **active, "cleared": now})
        if state["active"].get(aid) or cnt["over"]:
            counters[aid] = cnt
        else:
            counters.pop(aid, None)
    for aid, a in list(state["active"].items()):  # its subject is gone (a guest deleted, a disk pulled): dropped quietly
        if now - (a.get("seen") or a.get("since") or now) > 86400:
            del state["active"][aid]
            counters.pop(aid, None)
    state["history"] = [h for h in state["history"] if now - (h.get("cleared") or 0) < KEEP_S][:500]
    return events


def _load() -> dict:
    try:
        data = json.loads(ALARMS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"active": data.get("active") if isinstance(data.get("active"), dict) else {},
            "history": data.get("history") if isinstance(data.get("history"), list) else [],
            "counters": data.get("counters") if isinstance(data.get("counters"), dict) else {},
            "running": [v for v in data.get("running") or [] if isinstance(v, int)]}


def _save(state: dict) -> None:
    ALARMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ALARMS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, ALARMS_FILE)


def _push(e: dict) -> None:
    try:
        from tools.notify.send import send
        if e["event"] == "fired":
            send(e["message"], title=e["title"], priority="high" if e["severity"] == "critical" else "default",
                 tags=["rotating_light" if e["severity"] == "critical" else "warning"])
        else:
            send(f"Back to normal: {e['title']}.", title=f"OK: {e['title']}"[:120], priority="low",
                 tags=["white_check_mark"])
    except Exception:  # noqa: BLE001 - the push is a courtesy; the tab shows every alarm either way
        pass


def check(now: float | None = None) -> list[dict]:
    """One alarm pass: a fresh look, every rule, the state moved on and saved, and the pushes sent."""
    now = now or time.time()
    with _state_lock:
        state = _load()
        try:
            snap = _fresh(0)  # the full look: the tab's copy keeps only failed and backup tasks
            conds = conditions(snap, thresholds(), set(state["running"]), now, set(state["active"]))
            conds["unreachable"] = {"over": False, "under": True, "value": None, "severity": "critical",
                                    "title": "", "message": "", "sustain": UNREACHABLE_AFTER}
            state["running"] = [g["vmid"] for g in snap.get("guests") or [] if g.get("status") == "running"]
        except Exception as exc:  # noqa: BLE001 - Proxmox not answering is itself an alarm
            conds = {"unreachable": {"over": True, "under": False, "value": None, "severity": "critical",
                                     "title": "Can't reach Proxmox",
                                     "message": f"The Proxmox API isn't answering ({str(exc)[:160]}). The cluster may "
                                                "be fine, but nothing is being watched until it answers.",
                                     "sustain": UNREACHABLE_AFTER}}
        events = step(state, conds, now)
        _save(state)
    for e in events:
        _push(e)
    return events


def _loop() -> None:
    time.sleep(FIRST_S)
    while True:
        try:
            if _setting("proxmox_alarms_enabled") is not False:
                check()
        except Exception:  # noqa: BLE001 - the watch must never die
            pass
        time.sleep(CHECK_S)


def start() -> None:
    """The alarm checks, in the background; the server calls it once. Without a Proxmox token it does nothing."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    try:
        _px().token()
    except Exception:  # noqa: BLE001 - no Proxmox set up: nothing to watch
        return
    threading.Thread(target=_loop, daemon=True, name="proxmox-alarms").start()
