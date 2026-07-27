from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import UUID, uuid4, uuid5

from ..project_context import get_project_context
from .db import CURRENT_SCHEMA_VERSION, connect, database_path
from .event_repository import EventRepositoryError, normalize_timestamp


SCHEMA_NAME = "formal_log_database"
LEGACY_EVENT_NAMESPACE = UUID("5a60f6c1-a880-4ee5-b8e1-c9eea17c01f8")


class DatabaseMigrationError(RuntimeError):
    error_kind = "database_migration_error"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _schema_sql() -> str:
    return Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def _apply_schema(connection: sqlite3.Connection) -> None:
    for statement in _schema_sql().split(";"):
        if statement.strip():
            connection.execute(statement)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {"legacy_value": value}
    return parsed if isinstance(parsed, dict) else {"legacy_value": parsed}


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"running", "succeeded", "failed", "cancelled"}:
        return normalized
    if normalized in {"ok", "success", "successful", "complete", "completed"}:
        return "succeeded"
    if normalized in {"error", "errored", "failure"}:
        return "failed"
    return "succeeded"


def _normalize_level(value: Any) -> str:
    normalized = str(value or "info").strip().lower()
    aliases = {"warn": "warning", "fatal": "critical", "serial": "info"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"debug", "info", "warning", "error", "critical"}:
        return "info"
    return normalized


def _legacy_event_uuid(project_id: str, run_id: str, event_id: Any, row_number: int) -> str:
    stable_id = str(event_id).strip() if event_id else f"row:{row_number}"
    return str(uuid5(LEGACY_EVENT_NAMESPACE, f"{project_id}:{run_id}:{stable_id}"))


_PROJECT_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "hardwork_items": (
        "hardwork_id",
        "kind",
        "title",
        "raw_path",
        "processed_path",
        "source",
        "confidence",
        "created_at",
        "updated_at",
    ),
    "hardwork_audit": (
        "audit_id",
        "hardwork_id",
        "action",
        "old_value_json",
        "new_value_json",
        "reason",
        "created_at",
    ),
    "memory_items": (
        "memory_id",
        "namespace",
        "key",
        "value",
        "memory_type",
        "source",
        "confidence",
        "status",
        "created_at",
        "updated_at",
    ),
    "memory_audit": (
        "audit_id",
        "memory_id",
        "action",
        "old_value",
        "new_value",
        "reason",
        "created_at",
    ),
}
_MIGRATED_TABLES = ("events", "raw_logs", "errors", "runs", *_PROJECT_TABLE_COLUMNS)


def _row_value(row: sqlite3.Row, column: str, default: Any = None) -> Any:
    return row[column] if column in row.keys() else default


def _legacy_timestamp(value: Any) -> str:
    try:
        return normalize_timestamp(str(value or ""))
    except EventRepositoryError:
        return "1970-01-01T00:00:00+00:00"


def _copy_legacy_project_tables(connection: sqlite3.Connection, project_id: str) -> None:
    for table, columns in _PROJECT_TABLE_COLUMNS.items():
        legacy_table = f"{table}_legacy_v1"
        if legacy_table not in _tables(connection):
            continue
        quoted_columns = ", ".join(f'"{column}"' for column in ("project_id", *columns))
        placeholders = ", ".join("?" for _ in ("project_id", *columns))
        for row in connection.execute(f'SELECT rowid AS _rowid, * FROM "{legacy_table}"'):
            row_project_id = str(_row_value(row, "project_id", project_id) or project_id)
            values = (row_project_id, *(_row_value(row, column) for column in columns))
            connection.execute(
                f'INSERT OR IGNORE INTO "{table}" ({quoted_columns}) VALUES ({placeholders})',
                values,
            )


def _primary_key_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return tuple(row[1] for row in sorted(rows, key=lambda item: item[5]) if row[5] > 0)


def _migrate_v1(connection: sqlite3.Connection, project_id: str) -> None:
    existing_tables = _tables(connection)
    legacy_tables = [table for table in _MIGRATED_TABLES if table in existing_tables]
    for table in legacy_tables:
        connection.execute(f'ALTER TABLE "{table}" RENAME TO "{table}_legacy_v1"')

    _apply_schema(connection)
    _copy_legacy_project_tables(connection, project_id)

    legacy_runs = "runs_legacy_v1" in _tables(connection)
    if legacy_runs:
        for row in connection.execute('SELECT rowid AS _rowid, * FROM "runs_legacy_v1"'):
            payload: dict[str, Any] = {}
            if "project_dir" in row.keys() and row["project_dir"]:
                payload["legacy_project_dir"] = row["project_dir"]
            connection.execute(
                """
                INSERT OR IGNORE INTO runs (
                  project_id, run_id, task_type, status, started_at, ended_at,
                  next_sequence_no, selected_port, summary, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    project_id,
                    row["run_id"],
                    "legacy",
                    _normalize_status(row["status"] if "status" in row.keys() else None),
                    _legacy_timestamp(row["started_at"]),
                    _legacy_timestamp(row["ended_at"]) if "ended_at" in row.keys() and row["ended_at"] else None,
                    row["selected_port"] if "selected_port" in row.keys() else None,
                    row["summary"] if "summary" in row.keys() else None,
                    _json_text(payload),
                ),
            )

    sequence_by_run: defaultdict[str, int] = defaultdict(int)

    def ensure_run(run_id: str, timestamp: str, task_type: str) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO runs (
              project_id, run_id, task_type, status, started_at,
              next_sequence_no, payload_json
            ) VALUES (?, ?, ?, 'running', ?, 1, '{}')
            """,
            (project_id, run_id, task_type or "legacy", timestamp),
        )

    if "events_legacy_v1" in _tables(connection):
        rows = connection.execute('SELECT rowid AS _rowid, * FROM "events_legacy_v1" ORDER BY rowid').fetchall()
        for row in rows:
            run_id = str(row["run_id"])
            timestamp = _legacy_timestamp(row["ts"])
            tool = str(row["tool"] or "legacy")
            ensure_run(run_id, timestamp, tool)
            event_uuid = _legacy_event_uuid(
                project_id,
                run_id,
                row["event_id"] if "event_id" in row.keys() else None,
                int(row["_rowid"]),
            )
            payload = _json_object(row["data_json"] if "data_json" in row.keys() else None)
            identity = (
                project_id,
                run_id,
                timestamp,
                "unknown",
                _normalize_level(row["level"] if "level" in row.keys() else None),
                tool,
                str(row["source"] or "legacy") if "source" in row.keys() else "legacy",
                str(row["message"] or "") if "message" in row.keys() else "",
                _json_text(payload),
            )
            existing = connection.execute(
                """
                SELECT project_id, run_id, ts, phase, level, tool, source, message, payload_json
                FROM events WHERE event_uuid = ?
                """,
                (event_uuid,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != identity:
                    raise DatabaseMigrationError(f"legacy event UUID conflict: {event_uuid}")
                continue
            sequence_by_run[run_id] += 1
            connection.execute(
                """
                INSERT INTO events (
                  event_uuid, project_id, run_id, sequence_no, ts, phase, level,
                  tool, source, message, payload_json
                ) VALUES (?, ?, ?, ?, ?, 'unknown', ?, ?, ?, ?, ?)
                """,
                (
                    event_uuid,
                    project_id,
                    run_id,
                    sequence_by_run[run_id],
                    timestamp,
                    identity[4],
                    tool,
                    identity[6],
                    identity[7],
                    identity[8],
                ),
            )

    for run_id, last_sequence in sequence_by_run.items():
        connection.execute(
            "UPDATE runs SET next_sequence_no = ? WHERE project_id = ? AND run_id = ?",
            (last_sequence + 1, project_id, run_id),
        )

    if "raw_logs_legacy_v1" in _tables(connection):
        expected_raw_logs = connection.execute(
            'SELECT COUNT(*) FROM "raw_logs_legacy_v1"'
        ).fetchone()[0]
        for row in connection.execute('SELECT rowid AS _rowid, * FROM "raw_logs_legacy_v1"'):
            run_id = str(row["run_id"])
            ensure_run(run_id, str(row["created_at"] or _now_iso()), "legacy_raw_log")
            connection.execute(
                """
                INSERT INTO raw_logs (
                  project_id, raw_log_id, run_id, kind, path, created_at, sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    row["raw_log_id"],
                    run_id,
                    row["kind"],
                    row["path"],
                    row["created_at"],
                    row["sha256"],
                ),
            )
        copied_raw_logs = connection.execute(
            "SELECT COUNT(*) FROM raw_logs"
        ).fetchone()[0]
        if copied_raw_logs != expected_raw_logs:
            raise DatabaseMigrationError(
                f"v1 raw log migration count mismatch: "
                f"expected {expected_raw_logs}, copied {copied_raw_logs}"
            )

    if "errors_legacy_v1" in _tables(connection):
        expected_errors = connection.execute(
            'SELECT COUNT(*) FROM "errors_legacy_v1"'
        ).fetchone()[0]
        for row in connection.execute('SELECT rowid AS _rowid, * FROM "errors_legacy_v1"'):
            run_id = str(row["run_id"])
            ensure_run(run_id, str(row["created_at"] or _now_iso()), "legacy_error")
            connection.execute(
                """
                INSERT INTO errors (
                  project_id, error_id, run_id, error_kind, file, line, column,
                  exception_type, message, raw_text, recoverable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    row["error_id"],
                    run_id,
                    row["error_kind"],
                    row["file"],
                    row["line"],
                    row["column"],
                    row["exception_type"],
                    row["message"],
                    row["raw_text"],
                    row["recoverable"],
                    row["created_at"],
                ),
            )
        copied_errors = connection.execute(
            "SELECT COUNT(*) FROM errors"
        ).fetchone()[0]
        if copied_errors != expected_errors:
            raise DatabaseMigrationError(
                f"v1 error migration count mismatch: "
                f"expected {expected_errors}, copied {copied_errors}"
            )

    for table in (f"{name}_legacy_v1" for name in _MIGRATED_TABLES):
        if table in _tables(connection):
            connection.execute(f'DROP TABLE "{table}"')
    # A legacy index keeps its name when SQLite renames its table. Applying the
    # schema again after dropping legacy tables recreates any index whose name
    # was temporarily occupied by the legacy copy.
    _apply_schema(connection)


_V2_REBUILT_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "raw_logs": (
        "project_id",
        "raw_log_id",
        "run_id",
        "kind",
        "path",
        "created_at",
        "sha256",
    ),
    "errors": (
        "project_id",
        "error_id",
        "run_id",
        "error_kind",
        "file",
        "line",
        "column",
        "exception_type",
        "message",
        "raw_text",
        "recoverable",
        "created_at",
    ),
}


def _migrate_v2(
    connection: sqlite3.Connection,
    *,
    require_raw_error_tables: bool,
) -> None:
    existing_tables = _tables(connection)
    if require_raw_error_tables:
        missing_tables = set(_V2_REBUILT_TABLE_COLUMNS) - existing_tables
        if missing_tables:
            raise DatabaseMigrationError(
                f"schema v2 is missing required tables: {sorted(missing_tables)}"
            )
    for table, columns in _V2_REBUILT_TABLE_COLUMNS.items():
        if table not in existing_tables:
            continue
        actual_columns = _columns(connection, table)
        missing_columns = set(columns) - actual_columns
        unexpected_columns = actual_columns - set(columns)
        if missing_columns or unexpected_columns:
            raise DatabaseMigrationError(
                f"schema v2 table {table} does not match the lossless migration contract; "
                f"missing={sorted(missing_columns)}, "
                f"unexpected={sorted(unexpected_columns)}"
            )

    legacy_counts: dict[str, int] = {}
    for table in _V2_REBUILT_TABLE_COLUMNS:
        if table not in existing_tables:
            continue
        legacy_table = f"{table}_legacy_v2"
        if legacy_table in existing_tables:
            raise DatabaseMigrationError(
                f"cannot migrate v2 while stale table {legacy_table} exists"
            )
        legacy_counts[table] = int(
            connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        )
        connection.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy_table}"')

    _apply_schema(connection)

    for table, expected_count in legacy_counts.items():
        legacy_table = f"{table}_legacy_v2"
        columns = _V2_REBUILT_TABLE_COLUMNS[table]
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        connection.execute(
            f'INSERT INTO "{table}" ({quoted_columns}) '
            f'SELECT {quoted_columns} FROM "{legacy_table}"'
        )
        copied_count = int(
            connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        )
        if copied_count != expected_count:
            raise DatabaseMigrationError(
                f"v2 {table} migration count mismatch: "
                f"expected {expected_count}, copied {copied_count}"
            )

    for table in legacy_counts:
        connection.execute(f'DROP TABLE "{table}_legacy_v2"')

    # See the v1 migration note above: recreate indexes after old table-owned
    # index names have been released.
    _apply_schema(connection)


_V3_INDEXES: dict[str, tuple[str, tuple[str, ...]]] = {
    "idx_raw_logs_project_run_created": (
        "raw_logs",
        ("project_id", "run_id", "created_at", "raw_log_id"),
    ),
    "idx_raw_logs_project_kind_created": (
        "raw_logs",
        ("project_id", "kind", "created_at", "raw_log_id"),
    ),
    "idx_errors_project_run_created": (
        "errors",
        ("project_id", "run_id", "created_at", "error_id"),
    ),
    "idx_errors_project_kind_created": (
        "errors",
        ("project_id", "error_kind", "created_at", "error_id"),
    ),
}


def _validate_v3_foreign_key(connection: sqlite3.Connection, table: str) -> None:
    rows = connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    signature = {
        (
            row["table"],
            row["from"],
            row["to"],
            row["on_delete"],
        )
        for row in rows
    }
    expected = {
        ("runs", "project_id", "project_id", "CASCADE"),
        ("runs", "run_id", "run_id", "CASCADE"),
    }
    if len(rows) != 2 or len({row["id"] for row in rows}) != 1 or signature != expected:
        raise DatabaseMigrationError(
            f"schema v3 table {table} does not have the required composite runs foreign key"
        )


def _validate_v3_indexes(connection: sqlite3.Connection) -> None:
    for index_name, (table, expected_columns) in _V3_INDEXES.items():
        table_indexes = {
            row["name"]: row
            for row in connection.execute(f'PRAGMA index_list("{table}")')
        }
        index_metadata = table_indexes.get(index_name)
        rows = connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
        actual_columns = tuple(row["name"] for row in rows)
        if (
            index_metadata is None
            or bool(index_metadata["unique"])
            or bool(index_metadata["partial"])
            or actual_columns != expected_columns
        ):
            raise DatabaseMigrationError(
                f"schema v3 index {index_name} is not on {table} with columns "
                f"{expected_columns}; actual columns are {actual_columns}"
            )


def _validate_v3_checks(connection: sqlite3.Connection) -> None:
    project_id = f"schema-validation-{uuid4()}"
    run_id = f"schema-validation-{uuid4()}"
    created_at = _now_iso()
    connection.execute("SAVEPOINT validate_v3_checks")
    try:
        connection.execute(
            """
            INSERT INTO runs (
              project_id, run_id, task_type, status, started_at,
              next_sequence_no, payload_json
            ) VALUES (?, ?, 'schema_validation', 'running', ?, 1, '{}')
            """,
            (project_id, run_id, created_at),
        )
        invalid_rows = (
            (
                "empty raw kind",
                """
                INSERT INTO raw_logs (
                  project_id, raw_log_id, run_id, kind, path, created_at, sha256
                ) VALUES (?, ?, ?, ' ', 'sessions/capture.raw', ?, ?)
                """,
                (project_id, str(uuid4()), run_id, created_at, "a" * 64),
            ),
            (
                "absolute raw path",
                """
                INSERT INTO raw_logs (
                  project_id, raw_log_id, run_id, kind, path, created_at, sha256
                ) VALUES (?, ?, ?, 'capture', '/escape.raw', ?, ?)
                """,
                (project_id, str(uuid4()), run_id, created_at, "a" * 64),
            ),
            (
                "backslash raw path",
                """
                INSERT INTO raw_logs (
                  project_id, raw_log_id, run_id, kind, path, created_at, sha256
                ) VALUES (?, ?, ?, 'capture', ?, ?, ?)
                """,
                (
                    project_id,
                    str(uuid4()),
                    run_id,
                    r"sessions\capture.raw",
                    created_at,
                    "a" * 64,
                ),
            ),
            (
                "raw path traversal",
                """
                INSERT INTO raw_logs (
                  project_id, raw_log_id, run_id, kind, path, created_at, sha256
                ) VALUES (?, ?, ?, 'capture', '../escape.raw', ?, ?)
                """,
                (project_id, str(uuid4()), run_id, created_at, "a" * 64),
            ),
            (
                "raw SHA-256",
                """
                INSERT INTO raw_logs (
                  project_id, raw_log_id, run_id, kind, path, created_at, sha256
                ) VALUES (?, ?, ?, 'capture', 'sessions/capture.raw', ?, 'bad')
                """,
                (project_id, str(uuid4()), run_id, created_at),
            ),
            (
                "empty error kind",
                """
                INSERT INTO errors (
                  project_id, error_id, run_id, error_kind,
                  recoverable, created_at
                ) VALUES (?, ?, ?, ' ', 1, ?)
                """,
                (project_id, str(uuid4()), run_id, created_at),
            ),
            (
                "error line",
                """
                INSERT INTO errors (
                  project_id, error_id, run_id, error_kind, line,
                  recoverable, created_at
                ) VALUES (?, ?, ?, 'runtime', 'not-an-integer', 1, ?)
                """,
                (project_id, str(uuid4()), run_id, created_at),
            ),
            (
                "non-positive error line",
                """
                INSERT INTO errors (
                  project_id, error_id, run_id, error_kind, line,
                  recoverable, created_at
                ) VALUES (?, ?, ?, 'runtime', 0, 1, ?)
                """,
                (project_id, str(uuid4()), run_id, created_at),
            ),
            (
                "non-positive error column",
                """
                INSERT INTO errors (
                  project_id, error_id, run_id, error_kind, column,
                  recoverable, created_at
                ) VALUES (?, ?, ?, 'runtime', 0, 1, ?)
                """,
                (project_id, str(uuid4()), run_id, created_at),
            ),
            (
                "error recoverable",
                """
                INSERT INTO errors (
                  project_id, error_id, run_id, error_kind,
                  recoverable, created_at
                ) VALUES (?, ?, ?, 'runtime', 2, ?)
                """,
                (project_id, str(uuid4()), run_id, created_at),
            ),
        )
        for label, statement, parameters in invalid_rows:
            try:
                connection.execute(statement, parameters)
            except sqlite3.IntegrityError:
                continue
            raise DatabaseMigrationError(
                f"schema v3 CHECK contract did not reject invalid {label}"
            )
    finally:
        connection.execute("ROLLBACK TO validate_v3_checks")
        connection.execute("RELEASE validate_v3_checks")


def _validate_v3_contract(connection: sqlite3.Connection) -> None:
    expected_primary_keys = {
        "raw_logs": ("project_id", "raw_log_id"),
        "errors": ("project_id", "error_id"),
    }
    for table, expected_primary_key in expected_primary_keys.items():
        actual_primary_key = _primary_key_columns(connection, table)
        if actual_primary_key != expected_primary_key:
            raise DatabaseMigrationError(
                f"schema v3 table {table} has primary key {actual_primary_key}, "
                f"expected {expected_primary_key}"
            )
        _validate_v3_foreign_key(connection, table)
    _validate_v3_indexes(connection)
    _validate_v3_checks(connection)


def init_database(
    path: str | Path | None = None,
    *,
    project_id: str | None = None,
) -> Path:
    target = Path(path) if path is not None else database_path()
    connection = connect(target)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > CURRENT_SCHEMA_VERSION:
            raise DatabaseMigrationError(
                f"database schema version {version} is newer than supported version {CURRENT_SCHEMA_VERSION}"
            )
        tables = _tables(connection)
        runs_columns = _columns(connection, "runs") if "runs" in tables else set()
        events_columns = _columns(connection, "events") if "events" in tables else set()
        logs_current = {
            "project_id",
            "task_type",
            "next_sequence_no",
            "payload_json",
        } <= runs_columns and {
            "event_uuid",
            "project_id",
            "sequence_no",
            "phase",
            "payload_json",
        } <= events_columns
        project_tables_current = all(
            table not in tables
            or (
                "project_id" in _columns(connection, table)
                and _primary_key_columns(connection, table)[:1] == ("project_id",)
            )
            for table in _PROJECT_TABLE_COLUMNS
        )
        raw_log_columns = _columns(connection, "raw_logs") if "raw_logs" in tables else set()
        error_columns = _columns(connection, "errors") if "errors" in tables else set()
        raw_error_tables_current = {
            "project_id",
            "raw_log_id",
            "run_id",
            "kind",
            "path",
            "created_at",
            "sha256",
        } <= raw_log_columns and {
            "project_id",
            "error_id",
            "run_id",
            "error_kind",
            "file",
            "line",
            "column",
            "exception_type",
            "message",
            "raw_text",
            "recoverable",
            "created_at",
        } <= error_columns
        is_v2_shape = logs_current and project_tables_current
        is_v3_shape = is_v2_shape and raw_error_tables_current

        if not tables:
            _apply_schema(connection)
        elif version == CURRENT_SCHEMA_VERSION:
            if not is_v3_shape:
                raise DatabaseMigrationError(
                    "database is stamped as schema v3 but required tables or columns are missing"
                )
            _apply_schema(connection)
        elif version == 2:
            if not is_v2_shape:
                raise DatabaseMigrationError(
                    "database is stamped as schema v2 but required tables or columns are missing"
                )
            _migrate_v2(connection, require_raw_error_tables=True)
        elif is_v2_shape:
            _migrate_v2(connection, require_raw_error_tables=False)
        else:
            resolved_project_id = project_id
            if resolved_project_id is None:
                context = get_project_context()
                resolved_project_id = str(context["project_id"])
            _migrate_v1(connection, resolved_project_id)

        _validate_v3_contract(connection)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (CURRENT_SCHEMA_VERSION, SCHEMA_NAME, _now_iso()),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise DatabaseMigrationError(f"foreign key violations after migration: {violations}")
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        return target
    except DatabaseMigrationError:
        connection.rollback()
        raise
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise DatabaseMigrationError(f"database migration violated a constraint: {exc}") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
