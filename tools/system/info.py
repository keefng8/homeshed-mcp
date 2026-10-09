"""system.info. See ../../capabilities/system/info.md."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import time

from registry import tool


class SystemInfoError(RuntimeError):
    """A stat this platform genuinely can't provide failed in an unexpected way."""


def _memory_total_bytes() -> int | None:
    """POSIX-only (no stdlib equivalent on Windows) — fine here, this always runs inside the
    Linux container, never on the Windows host directly."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None


def _uptime_seconds() -> float | None:
    """Linux-only, reads /proc/uptime directly (no dependency for a two-field text file).

    NOT the container's own runtime — Docker doesn't namespace /proc/uptime by default, so this
    is the host kernel's uptime: a container recreated seconds earlier still reports days."""
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (FileNotFoundError, ValueError, OSError):
        return None


@tool(name="info", category="system", doc="system/info.md")
def info() -> dict:
    """Hardware/OS information — stdlib only, no `psutil` dependency for what's currently a
    handful of read-only stats.

    Reports what the server's own process sees: your machine when run with uvx; in Docker, the container's
    cgroup-limited view of `cpu_count`/`memory_total_bytes`/`disk` (its limits, not host monitoring).

    `uptime_seconds` is the one exception worth knowing: Docker doesn't namespace `/proc/uptime`
    by default, so in Docker this is the **host kernel's uptime**, not how long this container has
    been running. Don't use this field to answer "how long has the server been up."

    Returns:
        {hostname, platform, os_release, architecture, python_version, cpu_count,
         memory_total_bytes, disk: {total, used, free} (root filesystem, bytes),
         uptime_seconds}
        Any stat this platform can't provide is `null`, not omitted — a caller can rely on
        every key always being present.

    Raises:
        SystemInfoError: only for a genuinely unexpected failure — a missing stat is a `null`
            value, not an exception.
    """
    try:
        disk = shutil.disk_usage("/")
        return {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "memory_total_bytes": _memory_total_bytes(),
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
            "uptime_seconds": _uptime_seconds(),
        }
    except Exception as e:  # genuinely unexpected — every individual stat already fails soft
        raise SystemInfoError(f"could not gather system info: {e}") from e
