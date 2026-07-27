from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import RFC_4122, UUID, uuid5

from .event_repository import EventRepositoryError, normalize_timestamp


ERROR_NAMESPACE = UUID("ff8a1e42-9d03-41e6-b895-4d9a3d823297")


class ErrorRepositoryError(ValueError):
    error_kind = "error_repository_error"


class InvalidErrorRecordError(ErrorRepositoryError):
    error_kind = "invalid_error_record"


class ErrorConflictError(ErrorRepositoryError):
    error_kind = "error_conflict"


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise InvalidErrorRecordError(f"{field} is required")
    return normalized


def _optional_trimmed_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_content(value: Any) -> str | None:
    return None if value is None else str(value)


def _canonical_uuid(value: str) -> str:
    raw = str(value or "")
    try:
        parsed = UUID(raw)
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidErrorRecordError("error_id must be a canonical RFC 4122 UUID") from exc
    canonical = str(parsed)
    if raw != canonical or parsed.version is None or parsed.variant != RFC_4122:
        raise InvalidErrorRecordError("error_id must be a canonical RFC 4122 UUID")
    return canonical


def _positive_integer(value: int | None, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidErrorRecordError(f"{field} must be a positive integer or null")
    return value


def _recoverable(value: bool | int | None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise InvalidErrorRecordError("recoverable must be true, false, or null")


def _normalized_timestamp(value: str) -> str:
    try:
        return normalize_timestamp(value)
    except EventRepositoryError as exc:
        raise InvalidErrorRecordError(
            "created_at must be an ISO 8601 timestamp with a timezone"
        ) from exc


def stable_error_id(
    *,
    project_id: str,
    run_id: str,
    occurrence_key: str,
    error_kind: str,
    file: str | None,
    line: int | None,
    column: int | None,
    exception_type: str | None,
    message: str | None,
    raw_text: str | None,
) -> str:
    identity = json.dumps(
        [
            _required_text(project_id, "project_id"),
            _required_text(run_id, "run_id"),
            _required_text(occurrence_key, "occurrence_key"),
            _required_text(error_kind, "error_kind"),
            _optional_trimmed_text(file),
            _positive_integer(line, "line"),
            _positive_integer(column, "column"),
            _optional_trimmed_text(exception_type),
            _optional_content(message),
            _optional_content(raw_text),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid5(ERROR_NAMESPACE, identity))


def error_from_row(row: sqlite3.Row) -> dict[str, Any]:
    recoverable = row["recoverable"]
    return {
        "project_id": row["project_id"],
        "error_id": row["error_id"],
        "run_id": row["run_id"],
        "error_kind": row["error_kind"],
        "file": row["file"],
        "line": row["line"],
        "column": row["column"],
        "exception_type": row["exception_type"],
        "message": row["message"],
        "raw_text": row["raw_text"],
        "recoverable": None if recoverable is None else bool(recoverable),
        "created_at": row["created_at"],
    }


def _normalize_record(
    *,
    project_id: str,
    error_id: str,
    run_id: str,
    error_kind: str,
    file: str | None,
    line: int | None,
    column: int | None,
    exception_type: str | None,
    message: str | None,
    raw_text: str | None,
    recoverable: bool | int | None,
    created_at: str,
) -> dict[str, Any]:
    return {
        "project_id": _required_text(project_id, "project_id"),
        "error_id": _canonical_uuid(error_id),
        "run_id": _required_text(run_id, "run_id"),
        "error_kind": _required_text(error_kind, "error_kind"),
        "file": _optional_trimmed_text(file),
        "line": _positive_integer(line, "line"),
        "column": _positive_integer(column, "column"),
        "exception_type": _optional_trimmed_text(exception_type),
        "message": _optional_content(message),
        "raw_text": _optional_content(raw_text),
        "recoverable": _recoverable(recoverable),
        "created_at": _normalized_timestamp(created_at),
    }


def get_error(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    error_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM errors WHERE project_id = ? AND error_id = ?",
        (project_id, error_id),
    ).fetchone()
    return error_from_row(row) if row is not None else None


def insert_error(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    error_id: str,
    run_id: str,
    error_kind: str,
    file: str | None,
    line: int | None,
    column: int | None,
    exception_type: str | None,
    message: str | None,
    raw_text: str | None,
    recoverable: bool | int | None,
    created_at: str,
) -> tuple[dict[str, Any], bool]:
    expected = _normalize_record(
        project_id=project_id,
        error_id=error_id,
        run_id=run_id,
        error_kind=error_kind,
        file=file,
        line=line,
        column=column,
        exception_type=exception_type,
        message=message,
        raw_text=raw_text,
        recoverable=recoverable,
        created_at=created_at,
    )
    existing = get_error(
        connection,
        project_id=expected["project_id"],
        error_id=expected["error_id"],
    )
    if existing is not None:
        if existing != expected:
            raise ErrorConflictError(
                f"error_id {expected['error_id']} already identifies different content"
            )
        return existing, False
    run = connection.execute(
        "SELECT 1 FROM runs WHERE project_id = ? AND run_id = ?",
        (expected["project_id"], expected["run_id"]),
    ).fetchone()
    if run is None:
        raise InvalidErrorRecordError(
            f"run {expected['run_id']} does not exist in project {expected['project_id']}"
        )
    try:
        connection.execute(
            """
            INSERT INTO errors (
              project_id, error_id, run_id, error_kind, file, line, column,
              exception_type, message, raw_text, recoverable, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                expected["project_id"],
                expected["error_id"],
                expected["run_id"],
                expected["error_kind"],
                expected["file"],
                expected["line"],
                expected["column"],
                expected["exception_type"],
                expected["message"],
                expected["raw_text"],
                (
                    None
                    if expected["recoverable"] is None
                    else int(expected["recoverable"])
                ),
                expected["created_at"],
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise InvalidErrorRecordError(
            f"error record violates the database contract: {exc}"
        ) from exc
    stored = get_error(
        connection,
        project_id=expected["project_id"],
        error_id=expected["error_id"],
    )
    if stored is None:
        raise ErrorRepositoryError("error insert completed without a readable row")
    return stored, True


def list_errors_for_run(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM errors
        WHERE project_id = ? AND run_id = ?
        ORDER BY created_at, error_id
        """,
        (project_id, run_id),
    ).fetchall()
    return [error_from_row(row) for row in rows]
