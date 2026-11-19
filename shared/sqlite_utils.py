"""Shared SQLite WAL connection and BEGIN IMMEDIATE transaction helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def open_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open an isolated SQLite connection configured with WAL mode and busy timeout."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=10.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
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
