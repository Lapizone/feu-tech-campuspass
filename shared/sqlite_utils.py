"""Shared SQLite WAL connection and BEGIN IMMEDIATE transaction helpers."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_WAL_LOCK = threading.Lock()
_WAL_INITIALIZED: set[str] = set()


def open_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open an isolated SQLite connection configured with WAL mode and busy timeout."""
    path = Path(db_path).resolve()
    existed_before = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=10.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    db_key = str(path)
    with _WAL_LOCK:
        if not existed_before or db_key not in _WAL_INITIALIZED:
            conn.execute("PRAGMA journal_mode=WAL;")
            _WAL_INITIALIZED.add(db_key)
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Acquire a SQLite write lock up-front via BEGIN IMMEDIATE."""
    conn.execute("BEGIN IMMEDIATE;")
    try:
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
