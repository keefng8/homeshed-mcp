# Proxmox - Changes (owner approval)

## IDs
`proxmox.vm.power`, `proxmox.vm.snapshot`, `proxmox.vm.create`

## Purpose
Start, stop, shut down or reboot a VM or container; snapshot one; create a new one. WRITE-RISK. Settings, token and
errors are as in [read.md](read.md).

## Confirmation model: nothing runs from a tool call
1. `dry_run=true` (the default) validates the arguments and runs read-only checks (the node exists; the guest
   exists, is that kind on that node and isn't locked; for create, the vmid is free). It reports what would happen.
2. `dry_run=false` runs the same checks and **queues** the change in `proxmox_pending.py`
   (`DATA_DIR/usage/proxmox_pending.json`, or `PROXMOX_PENDING_FILE`). It returns `{"pending": true, "id": ...}`.
   Nothing has changed on Proxmox yet. This holds for every caller, the owner's own sessions included.
3. The owner decides on the admin routes (owner token only; client tokens can't reach them):
   - `GET /proxmox/pending` - what's waiting (newest first; each with who asked, the change and the checks).
   - `POST /proxmox/pending/<id>/approve` - re-validates the stored change, re-runs the checks, then calls Proxmox.
     Returns the task id (`upid`); follow it with `proxmox.tasks.list`.
   - `POST /proxmox/pending/<id>/decline` - drops it.
   A change waits at most 24 hours, then it's dropped (the guest may have changed since). A change that fails on
   approval stays waiting with `last_error`, so it can be retried or declined. At most 50 wait at once. Each step
   goes to the client audit trail (`proxmox change waiting/approved/declined/failed`).

There is deliberately **no delete** of any kind: no destroy, no snapshot delete or rollback, no disk removal.

## Parameters
| Tool | Parameters |
|---|---|
| `proxmox.vm.power` | `vmid`, `action` (`start`, `stop` = hard power-off, `shutdown` = ask the guest OS, `reboot`), `node` (default `""`: found cluster-wide from the vmid), `kind` (`qemu` default, `lxc`), `dry_run` (default true) |
| `proxmox.vm.snapshot` | `vmid`, `snapname` (letter first, 2-40 of letters/digits/`_`/`-`), `node` (default `""`: found cluster-wide), `kind`, `description` (default `""`, 200 chars), `dry_run` |
| `proxmox.vm.create` | `node` (required: any cluster node), `vmid` (must be free cluster-wide), `kind` (`qemu`/`lxc`, required), `options` (object of Proxmox's own create parameters), `dry_run` |

`options` for create: flat text/number/true-false values, at most 60, 500 characters each. Refused: `force`,
`archive`, `restore`, `unique`, `delete`, `purge`, `destroy-unreferenced-disks`, `vmid`, `node`, `password`,
`cipassword`, and any name containing `password`, `secret` or `token`. Set a root password in the Proxmox UI, or use
`ssh-public-keys` for lxc. An lxc container needs `ostemplate`.

## Returns
Dry run: `{dry_run: true, change, checks}` (+ `options` for create). Queued: `{pending: true, id, change,
expires_in_h, how}`. Approved (admin route): `{id, approved: true, change, result: {done, change, upid, checks, next}}`.

## Turning them on / who may use them
These tools are on by default because a call can only queue a change. Client grants: `"risk": "write"`, so
`read:*` or `read:proxmox` grants don't include them; grant `proxmox.*` or an exact id. To switch one off: the
Control Panel's tool switch (`POST /tools/<id>/disable`).

Token privileges: `VM.PowerMgmt` (power), `VM.Snapshot` (snapshot), `VM.Allocate` + `Datastore.AllocateSpace` +
`SDN.Use`/bridge access (create), on the paths involved. A narrow custom role is better than `root@pam`.

## Implementation
`mcp-server/tools/proxmox/vm_power.py`, `vm_snapshot.py`, `vm_create.py`; the queue in `mcp-server/proxmox_pending.py`;
the routes in `server.py`; the call itself in `tools/proxmox/_client.py`'s `execute()`. API calls:
`POST /nodes/{node}/{kind}/{vmid}/status/{action}`, `POST /nodes/{node}/{kind}/{vmid}/snapshot`,
`POST /nodes/{node}/{kind}`.

## Machine-readable definitions
`vm-power.json`, `vm-snapshot.json`, `vm-create.json` (same directory) - `"risk": "write"`.

## Tests
`tests/test_proxmox_changes.py`: a dry run and a queued change never send a POST; approval sends exactly one;
expiry, decline, failed approval, a tampered queue file, refused options, and the admin routes.

## Related
- [read.md](read.md)
- `docker.container.stop` (the dry-run pattern this extends), `memory_pending.py` (the approval queue it copies)
