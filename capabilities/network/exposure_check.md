# Network — Exposure Check

## ID
`network.exposure_check`

## Purpose
What your own public sites show the internet on the paths attackers try first: admin panels (`/admin`,
`/wp-admin/`, `/phpmyadmin/`), secret files (`/.env`, `/.git/HEAD`), and status pages (`/server-status`, `/actuator`).
Built after admin pages were found open by hand (Proxmox, an AI gateway, an old Strapi `/admin`).

## How it checks, and what it won't do
- **Read-only and light.** One `GET` per path, no logins, no redirects followed, and at most 10 hosts x 12 paths a
  call. Use it on your own sites.
- **Public hosts only.** Every address a name resolves to must be publicly routable, because the point is what the
  internet sees. A private or internal name is skipped with the reason. Each request goes to the address checked,
  with the site's own TLS name, never a second DNS lookup.
- **No false alarms from catch-all sites.** It first requests a random path. If the site answers that too (a
  single-page app), a 200 on `/admin` is reported as "answers every path", not as open.
- **Secret files count as open only when the content matches.** `/.env` needs `KEY=value` lines and `/.git/HEAD` a
  `ref:` line.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `hosts` | list of strings | required | Your sites' hostnames, optionally with `:port`, e.g. `["example.com", "admin.example.com:8443"]`. |
| `paths` | list of strings | the defaults above | Paths to try, each starting with `/`. For an admin app that lives at the root of its own name, pass `["/"]`. |

## Returns
`{hosts: [{host, checked, open, catch_all, findings: [{path, status, verdict}]} | {host, skipped}], open}`.
Paths that aren't there are left out of `findings`. Verdicts include "OPEN: answers without signing in",
"OPEN: a secret file is readable", "protected (Cloudflare Access)", "protected (redirects to a sign-in page)",
"protected", "answers every path (catch-all page)", "redirects to ...", and "no answer".

## Errors
`ExposureError` for no hosts, more than 10, more than 12 paths, a path not starting with `/`, or a malformed name.
A host that can't be resolved, or resolves privately, is listed with `skipped` rather than failing the call.

## Implementation
`tools/network/exposure_check.py`. Tests: `tests/test_exposure_check.py` (the network is faked).

## Machine-readable definition
`exposure_check.json`, same directory.

## Related
- `network.port_check`: is a port reachable at all.
- `network.dns.lookup`: what a name resolves to.
