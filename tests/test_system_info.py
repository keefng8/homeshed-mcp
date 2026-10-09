"""Tests for system.info. Runs for real (no mocking) — it's a thin stdlib wrapper over local
process/OS state, same reasoning as test_dns_lookup.py's real-resolution approach.
"""


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "system" and c.name == "info"]
    assert len(matches) == 1


def test_returns_all_keys():
    from tools.system.info import info

    result = info()
    expected_keys = {
        "hostname", "platform", "os_release", "architecture", "python_version",
        "cpu_count", "memory_total_bytes", "disk", "uptime_seconds",
    }
    assert set(result.keys()) == expected_keys


def test_hostname_and_platform_are_real():
    import platform
    import socket

    from tools.system.info import info

    result = info()
    assert result["hostname"] == socket.gethostname()
    assert result["platform"] == platform.system()
    assert result["python_version"] == platform.python_version()


def test_disk_stats_are_positive_and_consistent():
    from tools.system.info import info

    result = info()
    disk = result["disk"]
    assert disk["total"] > 0
    assert disk["used"] >= 0
    assert disk["free"] >= 0
    # used + free can be slightly less than total (reserved blocks) but never more.
    assert disk["used"] + disk["free"] <= disk["total"]


def test_cpu_count_is_positive_int():
    from tools.system.info import info

    result = info()
    assert isinstance(result["cpu_count"], int)
    assert result["cpu_count"] >= 1


def test_missing_stats_are_null_not_omitted(monkeypatch):
    """Force both POSIX-only helpers to fail, confirm the keys stay present as None rather
    than vanishing from the response."""
    import tools.system.info as module

    monkeypatch.setattr(module, "_memory_total_bytes", lambda: None)
    monkeypatch.setattr(module, "_uptime_seconds", lambda: None)

    result = module.info()
    assert result["memory_total_bytes"] is None
    assert result["uptime_seconds"] is None
    assert "memory_total_bytes" in result
    assert "uptime_seconds" in result
