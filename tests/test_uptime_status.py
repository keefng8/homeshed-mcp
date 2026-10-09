"""Tests for uptime.status. sqlite3 mocked throughout -- no live Kuma database needed. Real
live verification (the stale-container-id bug it caught, 2026-09-23) is recorded in
capabilities/uptime/status.md instead of a test, same convention as other capabilities whose
"actually works" claim is a live-verified doc note, not something reasserted on every CI run.
"""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "uptime" and c.name == "status"]
    assert len(matches) == 1


def _mock_connection(rows):
    con = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows
    con.cursor.return_value = cur
    con.__enter__ = lambda self: con
    return con


def test_happy_path_mixed_statuses():
    from tools.uptime import status as module

    rows = [
        (1, "Docker Vault Warden", "docker", 1, 1, "", "2026-09-23 15:33:16.058"),
        (8, "Docker mcp-server-mcp-server-1", "docker", 1, 0, "Request failed with status code 404", "2026-09-23 15:33:15.534"),
        (99, "Odd Monitor", "docker", 1, 5, "", "2026-09-23 15:33:16.058"),
    ]
    with patch("tools.uptime.status.os.path.exists", return_value=True), \
         patch("tools.uptime.status.sqlite3.connect", return_value=_mock_connection(rows)):
        result = module.status()

    assert result["summary"] == {"up": 1, "down": 1, "unknown": 1, "total": 3}
    assert result["monitors"][0]["status"] == "up"
    assert result["monitors"][1]["status"] == "down"
    assert result["monitors"][1]["message"] == "Request failed with status code 404"
    assert result["monitors"][2]["status"] == "unknown"


def test_missing_database_raises_clear_error():
    from tools.uptime import status as module

    with patch("tools.uptime.status.os.path.exists", return_value=False):
        with pytest.raises(module.UptimeStatusError, match="not found"):
            module.status()


def test_sqlite_error_raises_not_crashes():
    from tools.uptime import status as module
    import sqlite3

    with patch("tools.uptime.status.os.path.exists", return_value=True), \
         patch("tools.uptime.status.sqlite3.connect", side_effect=sqlite3.Error("disk I/O error")):
        with pytest.raises(module.UptimeStatusError, match="could not read"):
            module.status()


def test_empty_monitor_list():
    from tools.uptime import status as module

    with patch("tools.uptime.status.os.path.exists", return_value=True), \
         patch("tools.uptime.status.sqlite3.connect", return_value=_mock_connection([])):
        result = module.status()

    assert result == {"monitors": [], "summary": {"up": 0, "down": 0, "unknown": 0, "total": 0}}
