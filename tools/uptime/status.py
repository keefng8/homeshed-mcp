"""uptime.status. See ../../capabilities/uptime/status.md."""
from __future__ import annotations

import os
import sqlite3

from paths import data_path
from registry import tool

# Uptime Kuma's database, mounted read-only. KUMA_DB_PATH points elsewhere (release v1: no fixed /data path).
_DB_PATH = os.environ.get("KUMA_DB_PATH") or data_path("kuma/kuma.db")

# Kuma's numeric heartbeat.status column -- inferred from observed live data (0 seen for a
# real down container, 1 for real up ones), not from Kuma's own documentation (none found
# describing this column). Any other value maps to "unknown" rather than guessed.
_STATUS_MAP = {0: "down", 1: "up"}


class UptimeStatusError(RuntimeError):
    """The Kuma database isn't reachable or readable -- never for a real monitor being down,
    that's a normal, valid result, not an error."""


@tool(name="status", category="uptime", doc="uptime/status.md")
def status() -> dict:
    """Real Uptime Kuma monitor status, read directly from its SQLite database (read-only,
    same safe technique used throughout this project's Kuma work) -- no Kuma credentials
    needed, unlike adding/editing monitors which requires its authenticated Socket.IO API.

    Returns:
        {"monitors": [{"id", "name", "type", "active", "status", "message", "last_check"}],
        "summary": {"up", "down", "unknown", "total"}}

    Raises:
        UptimeStatusError: the database file isn't present (the read-only volume mount isn't
            configured) or isn't readable.
    """
    if not os.path.exists(_DB_PATH):
        raise UptimeStatusError(f"Kuma database not found at {_DB_PATH} -- is the read-only volume mounted?")

    try:
        con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=5.0)
        try:
            cur = con.cursor()
            cur.execute(
                """
                SELECT m.id, m.name, m.type, m.active, h.status, h.msg, h.time
                FROM monitor m
                LEFT JOIN heartbeat h ON h.id = (
                    SELECT MAX(id) FROM heartbeat WHERE monitor_id = m.id
                )
                ORDER BY m.id
                """
            )
            rows = cur.fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise UptimeStatusError(f"could not read the Kuma database: {e}") from None

    monitors = []
    counts = {"up": 0, "down": 0, "unknown": 0}
    for mon_id, name, mon_type, active, hb_status, msg, hb_time in rows:
        status_label = _STATUS_MAP.get(hb_status, "unknown")
        counts[status_label] += 1
        monitors.append(
            {
                "id": mon_id,
                "name": name,
                "type": mon_type,
                "active": bool(active),
                "status": status_label,
                "message": msg,
                "last_check": hb_time,
            }
        )

    return {
        "monitors": monitors,
        "summary": {**counts, "total": len(monitors)},
    }
