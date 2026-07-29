from __future__ import annotations

from pathlib import Path
import sqlite3
import stat
import time

from ..paths import data_dir


CURRENT_SCHEMA_VERSION = 3
DEFAULT_BUSY_TIMEOUT_MS = 5_000


def _enable_wal_with_retry(
    connection: sqlite3.Connection,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def database_path() -> Path:
    return data_dir() / "esp_mcp.sqlite"


def connect(
    path: str | Path | None = None,
    *,
    timeout_seconds: float = 5.0,
) -> sqlite3.Connection:
    target = Path(path) if path is not None else database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        target,
        timeout=timeout_seconds,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
        _enable_wal_with_retry(connection, timeout_seconds=timeout_seconds)
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection
    except Exception:
        connection.close()
        raise


def connect_readonly(
    path: str | Path,
    *,
    timeout_seconds: float = 5.0,
) -> sqlite3.Connection:
    """Open an existing SQLite database without creating or migrating it."""

    target = Path(path).resolve(strict=True)
    if not stat.S_ISREG(target.stat().st_mode):
        raise sqlite3.DatabaseError(
            f"SQLite database path is not a regular file: {target}"
        )
    connection = sqlite3.connect(
        f"{target.as_uri()}?mode=ro",
        uri=True,
        timeout=timeout_seconds,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
        return connection
    except Exception:
        connection.close()
        raise
