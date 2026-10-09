# System — Info

## ID
`system.info`

## Purpose
Hardware and OS information from the machine (or container) the server runs on. Standard library only,
no `psutil` dependency.

## Scope
Reports what the server's own process can see. Run with `uvx`, that's your machine. In Docker, it's the
container: `cpu_count`, `memory_total_bytes` and `disk` are the container's limited view, which is useful
for confirming its resource limits but is not host monitoring.

**In Docker, `uptime_seconds` is the host's uptime, not the container's.** Docker doesn't namespace
`/proc/uptime` by default, so a container started seconds ago still reports how long the host kernel has
been up. Don't use it to answer "how long has this container been running".

## Parameters
None.

## Returns
```
{
  hostname, platform, os_release, architecture, python_version, cpu_count,
  memory_total_bytes, disk: {total, used, free}, uptime_seconds
}
```
`disk` is the root filesystem, in bytes. Any stat this platform can't provide is `null`, not
omitted — every key is always present.

## Not supported
`memory_total_bytes` uses `os.sysconf` (POSIX only) and `uptime_seconds` reads `/proc/uptime`
(Linux only), so both are `null` on Windows and `uptime_seconds` is `null` on macOS.

## Errors
Raises `SystemInfoError` only for a genuinely unexpected failure — an individual missing stat is
a `null` value, never an exception.

## Implementation
`tools/system/info.py`.

## Machine-readable definition
`info.json`, same directory.

## Related
- `system.health`
