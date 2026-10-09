# Uptime — Status

## ID
`uptime.status`

## Purpose
Real [Uptime Kuma](https://github.com/louislam/uptime-kuma) monitor status, read directly from its
SQLite database, so no Kuma credentials are needed. (Adding or editing monitors needs Kuma's
authenticated API; just reading current status doesn't.)

## Parameters
None.

## Returns
```
{
  "monitors": [{"id", "name", "type", "active", "status", "message", "last_check"}],
  "summary": {"up": int, "down": int, "unknown": int, "total": int}
}
```
`status` is `"up"`/`"down"`/`"unknown"`, mapped from Kuma's numeric `heartbeat.status` column
(`0`→down, `1`→up, anything else→unknown). **This mapping is inferred from observed data**, not
from Kuma's documentation, which doesn't describe the column. Treat `"unknown"` as a real
possibility, not a bug.

## Configuration
`KUMA_DB_PATH`: the path to Kuma's `kuma.db` (default `DATA_DIR/kuma/kuma.db`). In Docker, mount
Kuma's data volume read-only into the server's container and point `KUMA_DB_PATH` at the file.

## Tip for Docker monitors
Kuma's `docker`-type monitor stores the container's *short ID*, which changes every time a
container is recreated (not just restarted), so after a redeploy the monitor reports
`Request failed with status code 404` while the container is healthy. The Docker API accepts a
container's **name** in place of its ID, and names are stable across recreates: set the monitor's
container to the name to avoid this.

## Errors
Raises `UptimeStatusError` if the database file isn't there or isn't readable. Never raises for a
monitor being down — that's a normal result in `status`.

## Security
Read-only file access to Kuma's own database — no network call, no credentials, no writes. SQLite's
WAL journaling means a single read could miss a write Kuma is making at that exact moment, which is
fine for an on-demand status check.

## Implementation
`tools/uptime/status.py`. Tests: `tests/test_uptime_status.py` (mocked sqlite3, no live Kuma
database needed).

## Machine-readable definition
`status.json`, same directory.

## Related
- `system.health` — the same idea (real reachability, never guessed) for the server's own backends.
