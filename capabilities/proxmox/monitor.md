# Proxmox monitor and alarms

## ID
`proxmox.health` (read), plus the Control Panel's **Proxmox** tab (owner routes `GET /proxmox/monitor` and
`GET /proxmox/monitor/history`) and the alarm checks behind both. Code: `mcp-server/proxmox_monitor.py`.

## Purpose
The owner, 2026-10-09: "a tab for: Monitoring my proxmox clusters. i need actual disc space, ram usages, and whatever
else i could use. i need full visuals on my vm's. with alarms." Built into the Control Panel rather than a separate
dashboard: it reuses the Proxmox API token the proxmox.* tools already use and the same phone alerts (notify.send).

## What it reads (all read-only)
| What | Proxmox API |
|---|---|
| Nodes, VMs, containers, storage: CPU, RAM, disk, network and disk counters | `GET /cluster/resources` |
| Quorum, node addresses | `GET /cluster/status` |
| A node's RAM (in use = total - available), swap, system disk, load, IO wait, CPU model, versions | `GET /nodes/{node}/status` |
| Physical disks: SMART health, remaining life ("wearout") | `GET /nodes/{node}/disks/list` (every 30 min) |
| Disk use inside a running VM | `GET /nodes/{node}/qemu/{vmid}/agent/get-fsinfo` (every 5 min; needs the QEMU guest agent in the VM) |
| Newest backup of each guest | every storage holding backups: `GET /nodes/{node}/storage/{storage}/content?content=backup` (every 30 min) |
| Recent tasks (stops, backups) | `GET /cluster/tasks` |
| Graphs | `GET /nodes/{node}[/qemu|lxc/{vmid}]/rrddata?timeframe=hour|day|week|month|year&cf=AVERAGE` |

A container's disk use comes from Proxmox itself. A VM without the guest agent shows only its disk's size
("no guest agent"). Network and disk speeds are worked out between two looks.

## Alarms
Every minute (a background thread the server starts; off with Settings > Alerts > **Proxmox alarms** >
"Proxmox alarms on your phone"). A rule over its line for its number of checks in a row fires ONE push; it clears after
2 checks back under by 3 points, with one "back to normal" push (low priority). State lives in
`usage/proxmox_alarms.json` (active alarms, 7 days of history, the guests running at the last check), so a restart
doesn't push again. An active alarm whose subject disappears (a guest deleted) is dropped quietly after a day.

| Rule | Line (setting) | Checks | Severity |
|---|---|---|---|
| Cluster lost quorum | - | 2 | urgent |
| Node offline | - | 2 | urgent |
| Proxmox API not answering | - | 3 | urgent |
| Node system disk, storage pool, disk inside a guest (file systems over 1 GiB) | `proxmox_alarm_disk` (85%) | 2 | urgent at 95% |
| Node RAM | `proxmox_alarm_node_ram` (90%) | 3 | warning |
| Node CPU | `proxmox_alarm_node_cpu` (95%) | 10 | warning |
| Guest RAM (includes its cache) | `proxmox_alarm_guest_ram` (98%) | 15 | warning |
| Running guest stopped with no stop/shutdown/migrate/rollback task in the last 15 min | `proxmox_alarm_guest_stopped` (on) | 1 | urgent, until it runs again |
| Guest with backups, newest older than the line | `proxmox_alarm_backup_days` (2 days) | 1 | warning |
| A node's newest finished backup task of the last day failed | - | 1 | warning |
| Physical disk SMART health not PASSED/OK | - | 2 | urgent |
| SSD life left at or under the line | `proxmox_alarm_disk_wear` (20%) | 2 | warning |

A "dir" storage the same size as its node's system disk is that disk: only the node's disk alarm fires for it.
Storage Proxmox reports as "unknown" on a node (defined cluster-wide, not present there) never alarms.

## proxmox.health
`proxmox.health()`: `{cluster, nodes: [{node, status, cpu_pct, ram_pct, disk_pct, disk_free_gb}], storage:
[{storage, node, used_pct, free_gb}], guests: {running, stopped, templates}, alarms: [{id, severity, title, since}],
checked_at}`.

## Tests
`mcp-server/tests/test_proxmox_monitor.py` (Proxmox faked).
