from __future__ import annotations

from .db import connect


def init_database() -> None:
    with connect() as conn:
        schema_path = __import__("pathlib").Path(__file__).with_name("schema.sql")
        conn.executescript(schema_path.read_text(encoding="utf-8"))

