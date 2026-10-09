"""Built-in memory (release v1, 2026-09-29): a small SQLite store the memory.* tools use when no memory-core server is
configured, so a new install remembers from the first minute with nothing else to run.

It keeps what memory-core's raw log (L0) keeps: messages grouped by agent and session, read back newest first in
memory-core's shapes, so every memory tool (capture, recall, the facts tier, recall_relevant's ranking) works the same
on either. Setting MEMORY_CORE_BASE_URL (and its keys) switches to memory-core; nothing is copied between the two.

The file lives at MEMORY_DB_PATH, else DATA_DIR/usage/memory.db (a volume in Docker).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone

from paths import data_path

_lock = threading.Lock()
_ready: set[str] = set()
SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    session TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_session ON messages (agent, session, at);
"""


def db_path() -> str:
    return os.environ.get("MEMORY_DB_PATH") or data_path("usage/memory.db")


def _connect() -> sqlite3.Connection:
    path = db_path()
    if path not in _ready:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    if path not in _ready:
        con.executescript(SCHEMA)
        _ready.add(path)
    return con


def _stamp(at: float) -> str:
    """memory-core's timestamp form: 2026-09-29T20:22:56.845Z."""
    return datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add(agent: str, session: str, role: str, content: str) -> str:
    """Saves one message; returns its id."""
    message_id = f"msg-{uuid.uuid4().hex}"
    with _lock, closing(_connect()) as con, con:
        con.execute("INSERT INTO messages (id, agent, session, role, content, at) VALUES (?, ?, ?, ?, ?, ?)",
                    (message_id, agent, session, role, content, time.time()))
    return message_id


def query(agent: str, session: str, limit: int) -> list[dict]:
    """The newest `limit` messages of one agent's session, newest first."""
    with closing(_connect()) as con:
        rows = con.execute("SELECT id, role, content, at FROM messages WHERE agent = ? AND session = ? "
                           "ORDER BY at DESC, rowid DESC LIMIT ?", (agent, session, max(0, int(limit)))).fetchall()
    return [{"id": i, "role": r, "content": c, "timestamp": _stamp(at)} for i, r, c, at in rows]


def count() -> int:
    """Every message saved here, for `homeshed-mcp doctor`. 0 before the first one (the file may not exist yet)."""
    if not os.path.exists(db_path()):
        return 0
    with closing(_connect()) as con:
        return con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
