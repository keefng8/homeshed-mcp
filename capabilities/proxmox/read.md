# Proxmox - Read-only tools

## IDs
`proxmox.nodes.list`, `proxmox.node.status`, `proxmox.vms.list`, `proxmox.vm.status`, `proxmox.storage.list`,
`proxmox.tasks.list`

## Purpose
See what a Proxmox VE cluster is doing: nodes, guests (VMs and LXC
containers), storage and recent tasks. Nothing here changes anything. Changes are in [changes.md](changes.md).
Added 2026-10-03: Proxmox tools that read the token server-side.

## Settings (by name; values never leave the server)
One API token covers the whole cluster. Requests go to one entry node (`PROXMOX_HOST`); Proxmox serves every node in the cluster through it.

| Name | Required | Default | Meaning |
|---|---|---|---|
| `PROXMOX_TOKEN_ID` | yes (or `PROXMOX_API_TOKEN`) | - | Either the **whole token**, `user@realm!tokenname=secret` (an optional `PVEAPIToken=` prefix is dropped), or just the id `user@realm!tokenname` with the secret below. |
| `PROXMOX_TOKEN_SECRET` | only with an id-only `TOKEN_ID` | - | The token's secret (UUID). When set it wins, and `TOKEN_ID` must then hold only the id. |
| `PROXMOX_API_TOKEN` | no | - | General name for the whole token, used when `PROXMOX_TOKEN_ID` isn't set. |
| `PROXMOX_HOST` | yes | - | `https://<address>:8006`, no path. |
| `PROXMOX_CA_PATH` | no | - | CA file to trust (copy of the node's `/etc/pve/pve-root-ca.pem`, inside the container). A fresh node's certificate is self-signed, so set this; TLS checking is never switched off. |

A malformed token gets an error naming the setting and the expected format, never any part of the value.

Each is looked up with `vault.secret()` on every call: the Control Panel's Credentials first, then the tool
server's environment. So a change in the vault applies at once, without a restart. The token goes into the
`Authorization: PVEAPIToken=<id>=<secret>` header and nowhere else; an error names a missing or bad setting, never
its value.

Token privileges for the read tools: `Sys.Audit` and `VM.Audit` (e.g. the built-in `PVEAuditor` role on `/`).

## Parameters
| Tool | Parameters |
|---|---|
| `proxmox.nodes.list` | none - every node in the cluster, plus `cluster` {name, quorate, nodes} (null on a standalone node) |
| `proxmox.node.status` | `node` (string, required) |
| `proxmox.vms.list` | cluster-wide (one `/cluster/resources` call); `node` filter (default `""` = all), `kind` (`all` default, `qemu`, `lxc`) |
| `proxmox.vm.status` | `vmid` (int), `node` (default `""`: found cluster-wide from the vmid), `kind` (`qemu` default, `lxc`) |
| `proxmox.storage.list` | `node` (default `""` = all) |
| `proxmox.tasks.list` | `node`, `limit` (1-50, default 20), `errors_only` (default false), `vmid` (default 0 = all) |

## Returns
Compact rows: memory and disk in GiB (2 decimals), CPU as a percentage, uptime in seconds, task times in UTC ISO
8601. `proxmox.vms.list` returns at most 100 guests (sorted by vmid) with `truncated` and a summary.

## Errors
`ProxmoxError`, always with a plain reason: a setting not set (named), a bad argument, Proxmox unreachable or slow
(10 s timeout, 5 s to connect), HTTP 401 (token refused), 403 (token lacks a privilege), an untrusted TLS
certificate (says which setting fixes it), or any other HTTP error with Proxmox's own reason.

## Implementation
`mcp-server/tools/proxmox/` - `_client.py` (settings, the one HTTP call, validation), one module per tool.
API calls: `GET /nodes`, `/cluster/status`, `/nodes/{node}/status`, `/cluster/resources?type=vm|storage`,
`/nodes/{node}/{qemu|lxc}/{vmid}/status/current`, `/nodes/{node}/tasks`.

## Machine-readable definitions
`nodes-list.json`, `node-status.json`, `vms-list.json`, `vm-status.json`, `storage-list.json`, `tasks-list.json`
(same directory) - `"risk": "read"`.

## Tests
`tests/test_proxmox_read.py` (httpx.MockTransport: no request leaves the process). Also checked against a real cluster
(2026-10-06).

## Related
- [changes.md](changes.md) - `proxmox.vm.power`, `proxmox.vm.snapshot`, `proxmox.vm.create`
- `system.health`, `uptime.status`
