# Approvals — Status

## ID
`approvals.status`

## Purpose
One answer to "what happened to my request?" across every queue that waits for the owner: apps' writes to the shared
memory, Proxmox changes, shop requests (Printify, Etsy, Pinterest) and messages to the owner. Check it rather than
assuming something still waits. The owner's own view is the admin route `GET /approvals/<id>` (404 when unknown).

## Parameters
| Name | Required | Notes |
|---|---|---|
| `request_id` | yes | The id the request was queued under (8 hex characters). |

## Returns
```
{"id", "queue": "memory" | "proxmox" | "commerce" | "inbox", "kind"?,
 "status": "waiting" | "approved" | "declined" | "expired" | "withdrawn" | "parked",
 "summary", "asked_at", "decided_at"?, "decided_by"?: "owner" | "bot:<name>" | "auto"}
```
or `{"id", "status": "unknown"}`.
- `kind`: a shop request's kind (`publish`, `update`, `etsy`, ...), a Proxmox change's op (`power`, `snapshot`,
  `create`, while it waits) or a message's need (`info`, `run_command`, `decision`, `other`).
- `decided_by`: `owner` (the Control Panel, or the owner's own approval inside an app), `bot:<name>` (a bot the owner
  lets approve) or `auto` (an app's AUTO mode). Left out where nobody decided: expired, withdrawn, parked.
- A message the owner marked done is `approved`. One its sender withdrew is gone: `unknown`.
- A Proxmox change not decided within 24 hours is `expired`; a memory write waits until decided.

## Who sees what
An app sees only its own requests: anyone else's id is `unknown`. The owner sees every request.

## Where it reads
`approvals.py`: each queue's file, the shop decisions file (30 days), and the client audit trail for memory and
Proxmox decisions (those queues drop a request once it's decided). A file that's missing or unreadable is skipped.
