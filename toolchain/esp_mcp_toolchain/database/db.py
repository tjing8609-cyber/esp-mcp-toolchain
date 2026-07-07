from __future__ import annotations

import sqlite3

from ..paths import data_dir


def database_path():
    return data_dir() / "esp_mcp.sqlite"


def connect() -> sqlite3.Connection:
    database_path().parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(database_path())

