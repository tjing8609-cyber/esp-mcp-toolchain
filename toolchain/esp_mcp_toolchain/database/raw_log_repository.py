from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
import sqlite3
from typing import Any
from uuid import RFC_4122, UUID, uuid5

from .event_repository import EventRepositoryError, normalize_timestamp


RAW_LOG_NAMESPACE = UUID("bc76398c-a842-47d6-8228-3e920f03cdab")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class RawLogRepositoryError(ValueError):
    error_kind = "raw_log_repository_error"


class InvalidRawLogError(RawLogRepositoryError):
    error_kind = "invalid_raw_log"


class RawLogConflictError(RawLogRepositoryError):
    error_kind = "raw_log_conflict"


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise InvalidRawLogError(f"{field} is required")
    return normalized


def _canonical_uuid(value: str) -> str:
    raw = str(value or "")
    try:
        parsed = UUID(raw)
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidRawLogError("raw_log_id must be a canonical RFC 4122 UUID") from exc
    canonical = str(parsed)
    if raw != canonical or parsed.version is None or parsed.variant != RFC_4122:
        raise InvalidRawLogError("raw_log_id must be a canonical RFC 4122 UUID")
    return canonical


def normalize_raw_log_path(value: str) -> str:
    raw = str(value or "").strip()
    if (
        not raw
        or raw.startswith("/")
        or "\\" in raw
        or ":" in raw
    ):
        raise InvalidRawLogError("path must be a relative POSIX path")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidRawLogError("path must not contain empty or traversal segments")
    normalized = PurePosixPath(*parts).as_posix()
    if normalized != raw:
        raise InvalidRawLogError("path must be a normalized relative POSIX path")
    return normalized


def normalize_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if _SHA256_PATTERN.fullmatch(raw) is None:
        raise InvalidRawLogError("sha256 must contain exactly 64 hexadecimal characters")
    return raw.lower()


def _normalized_timestamp(value: str) -> str:
    try:
        return normalize_timestamp(value)
    except EventRepositoryError as exc:
        raise InvalidRawLogError("created_at must be an ISO 8601 timestamp with a timezone") from exc


def stable_raw_log_id(
    *,
    project_id: str,
    run_id: str,
    kind: str,
    path: str,
) -> str:
    identity = json.dumps(
        [
            _required_text(project_id, "project_id"),
            _required_text(run_id, "run_id"),
            _required_text(kind, "kind"),
            normalize_raw_log_path(path),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid5(RAW_LOG_NAMESPACE, identity))


def raw_log_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["project_id"],
        "raw_log_id": row["raw_log_id"],
        "run_id": row["run_id"],
        "kind": row["kind"],
        "path": row["path"],
        "created_at": row["created_at"],
        "sha256": row["sha256"],
    }


def _normalize_record(
    *,
    project_id: str,
    raw_log_id: str,
    run_id: str,
    kind: str,
    path: str,
    created_at: str,
    sha256: str | None,
) -> dict[str, Any]:
    return {
        "project_id": _required_text(project_id, "project_id"),
        "raw_log_id": _canonical_uuid(raw_log_id),
        "run_id": _required_text(run_id, "run_id"),
        "kind": _required_text(kind, "kind"),
        "path": normalize_raw_log_path(path),
        "created_at": _normalized_timestamp(created_at),
        "sha256": normalize_sha256(sha256),
    }


def get_raw_log(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    raw_log_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM raw_logs WHERE project_id = ? AND raw_log_id = ?",
        (project_id, raw_log_id),
    ).fetchone()
    return raw_log_from_row(row) if row is not None else None


def insert_raw_log(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    raw_log_id: str,
    run_id: str,
    kind: str,
    path: str,
    created_at: str,
    sha256: str | None,
) -> tuple[dict[str, Any], bool]:
    expected = _normalize_record(
        project_id=project_id,
        raw_log_id=raw_log_id,
        run_id=run_id,
        kind=kind,
        path=path,
        created_at=created_at,
        sha256=sha256,
    )
    existing = get_raw_log(
        connection,
        project_id=expected["project_id"],
        raw_log_id=expected["raw_log_id"],
    )
    if existing is not None:
        if existing != expected:
            raise RawLogConflictError(
                f"raw_log_id {expected['raw_log_id']} already identifies different content"
            )
        return existing, False
    run = connection.execute(
        "SELECT 1 FROM runs WHERE project_id = ? AND run_id = ?",
        (expected["project_id"], expected["run_id"]),
    ).fetchone()
    if run is None:
        raise InvalidRawLogError(
            f"run {expected['run_id']} does not exist in project {expected['project_id']}"
        )
    try:
        connection.execute(
            """
            INSERT INTO raw_logs (
              project_id, raw_log_id, run_id, kind, path, created_at, sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(expected[column] for column in (
                "project_id",
                "raw_log_id",
                "run_id",
                "kind",
                "path",
                "created_at",
                "sha256",
            )),
        )
    except sqlite3.IntegrityError as exc:
        raise InvalidRawLogError(f"raw log violates the database contract: {exc}") from exc
    stored = get_raw_log(
        connection,
        project_id=expected["project_id"],
        raw_log_id=expected["raw_log_id"],
    )
    if stored is None:
        raise RawLogRepositoryError("raw log insert completed without a readable row")
    return stored, True


def list_raw_logs_for_run(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM raw_logs
        WHERE project_id = ? AND run_id = ?
        ORDER BY created_at, raw_log_id
        """,
        (project_id, run_id),
    ).fetchall()
    return [raw_log_from_row(row) for row in rows]
