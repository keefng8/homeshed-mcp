"""A fake Proxmox API for the proxmox.* tests: an httpx.MockTransport, so no request leaves the process."""
from __future__ import annotations

import json

import httpx

NODES = [{"node": "pve", "status": "online", "cpu": 0.0512, "maxcpu": 8, "mem": 4 * 1024 ** 3,
          "maxmem": 16 * 1024 ** 3, "uptime": 3600},
         {"node": "pve2", "status": "online", "cpu": 0.2, "maxcpu": 4, "mem": 2 * 1024 ** 3,
          "maxmem": 8 * 1024 ** 3, "uptime": 7200}]
CLUSTER = [{"type": "cluster", "name": "homelab", "quorate": 1, "nodes": 2},
           {"type": "node", "name": "pve", "online": 1}, {"type": "node", "name": "pve2", "online": 1}]
GUESTS = [
    {"vmid": 101, "name": "web", "type": "qemu", "node": "pve", "status": "running", "maxcpu": 2, "cpu": 0.1,
     "mem": 1024 ** 3, "maxmem": 2 * 1024 ** 3, "uptime": 120},
    {"vmid": 200, "name": "db", "type": "lxc", "node": "pve", "status": "stopped", "maxcpu": 1, "cpu": 0,
     "mem": 0, "maxmem": 1024 ** 3, "uptime": 0},
    {"vmid": 300, "name": "media", "type": "qemu", "node": "pve2", "status": "running", "maxcpu": 4, "cpu": 0.3,
     "mem": 3 * 1024 ** 3, "maxmem": 4 * 1024 ** 3, "uptime": 500},
]
STORAGE = [{"storage": "local-lvm", "node": "pve", "plugintype": "lvmthin", "status": "available",
            "content": "images,rootdir", "shared": 0, "disk": 25 * 1024 ** 3, "maxdisk": 100 * 1024 ** 3}]


class FakeProxmox:
    def __init__(self):
        self.calls: list[tuple[str, str, dict, str]] = []  # method, path, params, body
        self.status = {101: "running", 200: "stopped", 300: "running"}
        self.lock: dict[int, str] = {}
        self.fail: dict[str, int] = {}  # path -> status code to answer

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api2/json")
        body = request.content.decode() if request.content else ""
        self.calls.append((request.method, path, dict(request.url.params), body))
        if path in self.fail:
            return httpx.Response(self.fail[path], json={"data": None}, extensions={"reason_phrase": b"nope"})
        if request.method == "GET":
            if path == "/nodes":
                return self._ok(NODES)
            if path == "/cluster/status":
                return self._ok(CLUSTER)
            if path == "/nodes/pve/status":
                return self._ok({"uptime": 3600, "cpu": 0.25, "cpuinfo": {"cpus": 8, "model": "Test CPU"},
                                 "loadavg": ["0.5", "0.4", "0.3"], "memory": {"used": 2 * 1024 ** 3, "total": 16 * 1024 ** 3},
                                 "swap": {"used": 0, "total": 0}, "rootfs": {"used": 10 * 1024 ** 3, "total": 50 * 1024 ** 3},
                                 "pveversion": "pve-manager/8.2.4", "current-kernel": {"release": "6.8.12-1-pve"}})
            if path == "/cluster/resources":
                return self._ok(GUESTS if request.url.params.get("type") == "vm" else STORAGE)
            if path.endswith("/status/current"):
                vmid = int(path.split("/")[4])
                g = next(x for x in GUESTS if x["vmid"] == vmid)
                return self._ok({"name": g["name"], "status": self.status[vmid], "lock": self.lock.get(vmid),
                                 "cpus": g["maxcpu"], "cpu": 0.1, "mem": g["mem"], "maxmem": g["maxmem"]})
            if path == "/nodes/pve/tasks":
                return self._ok([{"upid": "UPID:pve:1", "type": "qmstart", "id": "101", "user": "root@pam",
                                  "status": "OK", "starttime": 1790000000, "endtime": 1790000005}] * 3)
        if request.method == "POST":
            return self._ok("UPID:pve:00001234:task")
        return httpx.Response(404, json={"data": None})

    @staticmethod
    def _ok(data):
        return httpx.Response(200, content=json.dumps({"data": data}).encode(),
                              headers={"content-type": "application/json"})

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]
