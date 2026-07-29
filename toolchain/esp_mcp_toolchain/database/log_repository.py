from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from uuid import UUID, uuid5

from .db import CURRENT_SCHEMA_VERSION, connect, connect_readonly
from .error_repository import (
    ErrorRepositoryError,
    get_error as select_error,
    insert_error,
    list_errors_for_run,
    list_recent_errors_for_run,
    stable_error_id,
)
from .event_repository import (
    EventRepositoryError,
    get_event_by_uuid,
    insert_event,
    list_events_for_run,
    normalize_event_uuid,
    normalize_level,
    normalize_phase,
    normalize_timestamp,
    payload_from_text,
    payload_to_text,
    query_events as select_events,
)
from .raw_log_repository import (
    RawLogRepositoryError,
    get_raw_log as select_raw_log,
    insert_raw_log,
    list_raw_logs_for_run,
    list_recent_raw_logs_for_run,
    normalize_raw_log_path,
    normalize_sha256,
    stable_raw_log_id,
)


RUN_STATUSES = {"running", "succeeded", "failed", "cancelled"}
LEGACY_JSONL_NAMESPACE = UUID("28446ce5-4840-4d6d-a354-187721231ff8")
_HISTORICAL_EVENT_PROFILE_FIELDS = frozenset(
    {
        "event_uuid",
        "project_id",
        "run_id",
        "ts",
        "phase",
        "level",
        "tool",
        "source",
        "message",
        "payload_json",
    }
)
_HISTORICAL_RUN_PROFILE_FIELDS = frozenset(
    {
        "project_id",
        "run_id",
        "task_type",
        "status",
        "selected_port",
        "terminal_event_uuid",
    }
)


class LogRepositoryError(RuntimeError):
    error_kind = "log_repository_error"


class RunNotFoundError(LogRepositoryError):
    error_kind = "run_not_found"


class RunConflictError(LogRepositoryError):
    error_kind = "run_id_conflict"


class RunNotRunningError(LogRepositoryError):
    error_kind = "run_not_running"


class RunNotTerminalError(LogRepositoryError):
    error_kind = "run_not_terminal"


class EventNotFoundError(LogRepositoryError):
    error_kind = "event_not_found"


class EventNotTerminalError(LogRepositoryError):
    error_kind = "event_not_terminal"


class RunStateConflictError(LogRepositoryError):
    error_kind = "run_state_conflict"


class NativeRunImportConflictError(RunConflictError):
    error_kind = "native_run_import_conflict"


class ArtifactProjectionError(LogRepositoryError):
    error_kind = "artifact_projection_failed"


class RunTaskTypeConflictError(RunConflictError):
    error_kind = "run_task_type_conflict"


class HistoricalArtifactProfileConflictError(LogRepositoryError):
    error_kind = "historical_artifact_database_profile_conflict"


class HistoricalRawClaimConflictError(LogRepositoryError):
    error_kind = "historical_artifact_raw_claim_conflict"


class LogDatabaseQueryError(LogRepositoryError):
    recoverable = False


class LogDatabaseInvalidError(LogDatabaseQueryError):
    error_kind = "log_database_invalid"


class LogDatabaseUnavailableError(LogDatabaseQueryError):
    error_kind = "log_database_unavailable"
    recoverable = True


class LogDatabaseSchemaUnsupportedError(LogDatabaseQueryError):
    error_kind = "log_database_schema_unsupported"


@dataclass(frozen=True)
class RawLogArtifact:
    kind: str
    path: str
    sha256: str | None = None


@dataclass(frozen=True)
class ErrorArtifact:
    occurrence_key: str
    error_kind: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    exception_type: str | None = None
    message: str | None = None
    raw_text: str | None = None
    recoverable: bool | int | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class EventArtifacts:
    raw_logs: tuple[RawLogArtifact, ...] = ()
    errors: tuple[ErrorArtifact, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.raw_logs, tuple) or not all(
            isinstance(item, RawLogArtifact) for item in self.raw_logs
        ):
            raise TypeError(
                "EventArtifacts.raw_logs must be a tuple of RawLogArtifact values"
            )
        if not isinstance(self.errors, tuple) or not all(
            isinstance(item, ErrorArtifact) for item in self.errors
        ):
            raise TypeError(
                "EventArtifacts.errors must be a tuple of ErrorArtifact values"
            )


@dataclass(frozen=True)
class HistoricalRawClaim:
    path: str
    kind: str
    sha256: str
    adapter_id: str
    reconciliation_version: int
    event_profile_sha256: str
    artifact_bundle_sha256: str


EMPTY_EVENT_ARTIFACTS = EventArtifacts()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["project_id"],
        "run_id": row["run_id"],
        "task_type": row["task_type"],
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "next_sequence_no": row["next_sequence_no"],
        "selected_port": row["selected_port"],
        "summary": row["summary"],
        "payload_json": payload_from_text(row["payload_json"]),
    }


def canonical_profile_sha256(profile: dict[str, Any]) -> str:
    if not isinstance(profile, dict):
        raise TypeError("profile must be a dictionary")
    try:
        encoded = json.dumps(
            profile,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LogRepositoryError(
            "historical artifact profile must be JSON serializable"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def event_artifact_bundle_sha256(artifacts: EventArtifacts) -> str:
    if not isinstance(artifacts, EventArtifacts):
        raise TypeError("artifacts must be an EventArtifacts value")
    bundle = {
        "raw_logs": [
            {
                "kind": artifact.kind,
                "path": artifact.path,
                "sha256": artifact.sha256,
            }
            for artifact in artifacts.raw_logs
        ],
        "errors": [
            {
                "occurrence_key": artifact.occurrence_key,
                "error_kind": artifact.error_kind,
                "file": artifact.file,
                "line": artifact.line,
                "column": artifact.column,
                "exception_type": artifact.exception_type,
                "message": artifact.message,
                "raw_text": artifact.raw_text,
                "recoverable": artifact.recoverable,
                "created_at": artifact.created_at,
            }
            for artifact in artifacts.errors
        ],
    }
    return canonical_profile_sha256(bundle)


def _normalized_historical_claim(
    claim: HistoricalRawClaim,
) -> dict[str, Any]:
    if not isinstance(claim, HistoricalRawClaim):
        raise TypeError(
            "raw_claims must contain only HistoricalRawClaim values"
        )
    kind = str(claim.kind or "").strip()
    adapter_id = str(claim.adapter_id or "").strip()
    if not kind:
        raise LogRepositoryError("historical raw claim kind is required")
    if not adapter_id:
        raise LogRepositoryError("historical raw claim adapter_id is required")
    if (
        isinstance(claim.reconciliation_version, bool)
        or not isinstance(claim.reconciliation_version, int)
        or claim.reconciliation_version < 1
    ):
        raise LogRepositoryError(
            "historical raw claim reconciliation_version must be positive"
        )
    sha256 = normalize_sha256(claim.sha256)
    event_profile_sha256 = normalize_sha256(
        claim.event_profile_sha256
    )
    artifact_bundle_sha256 = normalize_sha256(
        claim.artifact_bundle_sha256
    )
    if (
        sha256 is None
        or event_profile_sha256 is None
        or artifact_bundle_sha256 is None
    ):
        raise LogRepositoryError(
            "historical raw claim digests are required"
        )
    return {
        "path": normalize_raw_log_path(claim.path),
        "kind": kind,
        "sha256": sha256,
        "adapter_id": adapter_id,
        "reconciliation_version": claim.reconciliation_version,
        "event_profile_sha256": event_profile_sha256,
        "artifact_bundle_sha256": artifact_bundle_sha256,
    }


def _historical_claim_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["project_id"],
        "path": row["path"],
        "run_id": row["run_id"],
        "event_uuid": row["event_uuid"],
        "kind": row["kind"],
        "sha256": row["sha256"],
        "adapter_id": row["adapter_id"],
        "reconciliation_version": row["reconciliation_version"],
        "event_profile_sha256": row["event_profile_sha256"],
        "artifact_bundle_sha256": row["artifact_bundle_sha256"],
        "claimed_at": row["claimed_at"],
    }


def _insert_historical_raw_claim(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    run_id: str,
    event_uuid: str,
    claimed_at: str,
    claim: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    expected = {
        "project_id": project_id,
        "path": claim["path"],
        "run_id": run_id,
        "event_uuid": event_uuid,
        "kind": claim["kind"],
        "sha256": claim["sha256"],
        "adapter_id": claim["adapter_id"],
        "reconciliation_version": claim["reconciliation_version"],
        "event_profile_sha256": claim["event_profile_sha256"],
        "artifact_bundle_sha256": claim["artifact_bundle_sha256"],
        "claimed_at": normalize_timestamp(claimed_at),
    }
    existing_row = connection.execute(
        """
        SELECT * FROM historical_raw_claims
        WHERE project_id = ? AND path = ?
        """,
        (project_id, claim["path"]),
    ).fetchone()
    if existing_row is not None:
        existing = _historical_claim_from_row(existing_row)
        if existing != expected:
            raise HistoricalRawClaimConflictError(
                "historical raw path is already owned by another "
                "run, event, profile, or artifact bundle"
            )
        return existing, False
    try:
        connection.execute(
            """
            INSERT INTO historical_raw_claims (
              project_id, path, run_id, event_uuid, kind, sha256,
              adapter_id, reconciliation_version, event_profile_sha256,
              artifact_bundle_sha256, claimed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(
                expected[column]
                for column in (
                    "project_id",
                    "path",
                    "run_id",
                    "event_uuid",
                    "kind",
                    "sha256",
                    "adapter_id",
                    "reconciliation_version",
                    "event_profile_sha256",
                    "artifact_bundle_sha256",
                    "claimed_at",
                )
            ),
        )
    except sqlite3.IntegrityError:
        conflicting_row = connection.execute(
            """
            SELECT * FROM historical_raw_claims
            WHERE project_id = ? AND path = ?
            """,
            (project_id, claim["path"]),
        ).fetchone()
        if conflicting_row is not None:
            raise HistoricalRawClaimConflictError(
                "historical raw path acquired a conflicting owner"
            )
        raise
    stored_row = connection.execute(
        """
        SELECT * FROM historical_raw_claims
        WHERE project_id = ? AND path = ?
        """,
        (project_id, claim["path"]),
    ).fetchone()
    if stored_row is None:
        raise LogRepositoryError(
            "historical raw claim insert completed without a readable row"
        )
    return _historical_claim_from_row(stored_row), True


def _require_profile_shape(
    expected: dict[str, Any] | None,
    *,
    label: str,
    required_fields: frozenset[str],
) -> None:
    if expected is None:
        return
    if not isinstance(expected, dict):
        raise TypeError(f"expected_{label}_profile must be a dictionary")
    canonical_profile_sha256(expected)
    supplied_fields = frozenset(expected)
    if supplied_fields != required_fields:
        missing = sorted(required_fields - supplied_fields)
        unexpected = sorted(supplied_fields - required_fields)
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unexpected:
            details.append(f"unexpected={','.join(unexpected)}")
        raise HistoricalArtifactProfileConflictError(
            f"historical {label} profile has an invalid field set: "
            f"{'; '.join(details)}"
        )


def _require_profile_match(
    actual: dict[str, Any],
    expected: dict[str, Any] | None,
    *,
    label: str,
    required_fields: frozenset[str],
    event_ts_tolerance_seconds: float = 0.0,
) -> None:
    _require_profile_shape(
        expected,
        label=label,
        required_fields=required_fields,
    )
    if expected is None:
        return
    mismatched: list[str] = []
    for key, value in expected.items():
        if key not in actual:
            mismatched.append(key)
            continue
        if label == "event" and key == "ts":
            try:
                actual_ts = datetime.fromisoformat(
                    normalize_timestamp(str(actual[key]))
                )
                expected_ts = datetime.fromisoformat(
                    normalize_timestamp(str(value))
                )
                difference = abs((actual_ts - expected_ts).total_seconds())
            except (EventRepositoryError, TypeError, ValueError):
                mismatched.append(key)
                continue
            if difference > event_ts_tolerance_seconds:
                mismatched.append(key)
            continue
        if actual[key] != value:
            mismatched.append(key)
    mismatched.sort()
    if mismatched:
        raise HistoricalArtifactProfileConflictError(
            f"historical {label} profile conflicts in fields: "
            f"{', '.join(mismatched)}"
        )


def _get_run_row(connection: sqlite3.Connection, project_id: str, run_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM runs WHERE project_id = ? AND run_id = ?",
        (project_id, run_id),
    ).fetchone()


_READONLY_BASE_COLUMNS: dict[str, frozenset[str]] = {
    "runs": frozenset(
        {
            "project_id",
            "run_id",
            "task_type",
            "status",
            "started_at",
            "ended_at",
            "next_sequence_no",
            "selected_port",
            "summary",
            "payload_json",
        }
    ),
    "events": frozenset(
        {
            "event_uuid",
            "project_id",
            "run_id",
            "sequence_no",
            "ts",
            "phase",
            "level",
            "tool",
            "source",
            "message",
            "payload_json",
        }
    ),
}
_READONLY_V3_COLUMNS: dict[str, frozenset[str]] = {
    "raw_logs": frozenset(
        {"project_id", "raw_log_id", "run_id", "kind", "path", "created_at", "sha256"}
    ),
    "errors": frozenset(
        {
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
        }
    ),
    "historical_raw_claims": frozenset(
        {
            "project_id",
            "path",
            "run_id",
            "event_uuid",
            "kind",
            "sha256",
            "adapter_id",
            "reconciliation_version",
            "event_profile_sha256",
            "artifact_bundle_sha256",
            "claimed_at",
        }
    ),
}


def _readonly_schema_version(connection: sqlite3.Connection) -> int:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version not in {2, CURRENT_SCHEMA_VERSION}:
        raise LogDatabaseSchemaUnsupportedError(
            f"SQLite log database schema v{version} is not supported for read-only queries; "
            f"supported versions are v2 and v{CURRENT_SCHEMA_VERSION}."
        )
    required = dict(_READONLY_BASE_COLUMNS)
    if version == CURRENT_SCHEMA_VERSION:
        required.update(_READONLY_V3_COLUMNS)
    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    for table, expected_columns in required.items():
        if table not in table_names:
            raise LogDatabaseSchemaUnsupportedError(
                f"SQLite log database schema v{version} is missing required table {table}."
            )
        actual_columns = {
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        missing_columns = expected_columns - actual_columns
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise LogDatabaseSchemaUnsupportedError(
                f"SQLite log database schema v{version} table {table} "
                f"is missing required columns: {missing}."
            )
    return version


def _classify_sqlite_query_error(exc: sqlite3.DatabaseError) -> LogDatabaseQueryError:
    error_code = getattr(exc, "sqlite_errorcode", None)
    primary_code = error_code & 0xFF if isinstance(error_code, int) else None
    unavailable_codes = {
        getattr(sqlite3, "SQLITE_BUSY", 5),
        getattr(sqlite3, "SQLITE_LOCKED", 6),
        getattr(sqlite3, "SQLITE_CANTOPEN", 14),
        getattr(sqlite3, "SQLITE_PERM", 3),
        getattr(sqlite3, "SQLITE_READONLY", 8),
        getattr(sqlite3, "SQLITE_IOERR", 10),
    }
    message = str(exc).lower()
    unavailable_markers = (
        "locked",
        "busy",
        "unable to open",
        "permission denied",
        "readonly",
        "read-only",
        "disk i/o",
    )
    if primary_code in unavailable_codes or any(
        marker in message for marker in unavailable_markers
    ):
        return LogDatabaseUnavailableError(
            "SQLite log database is temporarily unavailable for read-only queries."
        )
    return LogDatabaseInvalidError(
        "SQLite log database is invalid or cannot be read safely."
    )


def _open_readonly_snapshot(
    database: str | Path,
) -> tuple[sqlite3.Connection, int]:
    connection: sqlite3.Connection | None = None
    try:
        connection = connect_readonly(database)
        connection.execute("BEGIN")
        return connection, _readonly_schema_version(connection)
    except FileNotFoundError:
        if connection is not None:
            connection.close()
        raise
    except LogDatabaseQueryError:
        if connection is not None:
            connection.close()
        raise
    except sqlite3.DatabaseError as exc:
        if connection is not None:
            connection.close()
        raise _classify_sqlite_query_error(exc) from exc
    except OSError as exc:
        if connection is not None:
            connection.close()
        raise LogDatabaseUnavailableError(
            "SQLite log database path is unavailable for read-only queries."
        ) from exc


def create_run(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
    task_type: str,
    started_at: str,
    selected_port: str | None = None,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    if not project_id.strip() or not run_id.strip() or not task_type.strip():
        raise RunConflictError("project_id, run_id, and task_type are required")
    payload_text = payload_to_text(payload)
    normalized_started_at = normalize_timestamp(started_at)
    connection = connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = _get_run_row(connection, project_id, run_id)
        if existing is not None:
            if existing["task_type"] != task_type:
                raise RunConflictError(
                    f"run_id {run_id} already exists with task_type {existing['task_type']}"
                )
            if selected_port and existing["selected_port"] not in {None, selected_port}:
                raise RunConflictError(
                    f"run_id {run_id} already exists with selected_port {existing['selected_port']}"
                )
            if selected_port and existing["selected_port"] is None:
                connection.execute(
                    "UPDATE runs SET selected_port = ? WHERE project_id = ? AND run_id = ?",
                    (selected_port, project_id, run_id),
                )
                existing = _get_run_row(connection, project_id, run_id)
            connection.commit()
            return _run_from_row(existing), False
        connection.execute(
            """
            INSERT INTO runs (
              project_id, run_id, task_type, status, started_at,
              next_sequence_no, selected_port, summary, payload_json
            ) VALUES (?, ?, ?, 'running', ?, 1, ?, ?, ?)
            """,
            (project_id, run_id, task_type, normalized_started_at, selected_port, summary, payload_text),
        )
        row = _get_run_row(connection, project_id, run_id)
        connection.commit()
        return _run_from_row(row), True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def get_run(database: str | Path, *, project_id: str, run_id: str) -> dict[str, Any] | None:
    connection = connect(database)
    try:
        row = _get_run_row(connection, project_id, run_id)
        return _run_from_row(row) if row is not None else None
    finally:
        connection.close()


def get_event(database: str | Path, *, event_uuid: str) -> dict[str, Any] | None:
    connection = connect(database)
    try:
        return get_event_by_uuid(connection, normalize_event_uuid(event_uuid))
    finally:
        connection.close()


def finish_run(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
    status: str,
    ended_at: str,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_status = str(status).strip().lower()
    if normalized_status not in RUN_STATUSES - {"running"}:
        raise LogRepositoryError("finished run status must be succeeded, failed, or cancelled")
    normalized_ended_at = normalize_timestamp(ended_at)
    connection = connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = _get_run_row(connection, project_id, run_id)
        if row is None:
            raise RunNotFoundError(f"No run {run_id} exists in project {project_id}")
        if row["status"] != "running":
            if row["status"] != normalized_status:
                raise RunStateConflictError(
                    f"run {run_id} is already {row['status']} and cannot become {normalized_status}"
                )
            connection.commit()
            return _run_from_row(row)
        merged_payload = payload_from_text(row["payload_json"])
        if payload:
            if not isinstance(payload, dict):
                raise LogRepositoryError("run payload_json must be a JSON object")
            merged_payload.update(payload)
        connection.execute(
            """
            UPDATE runs
            SET status = ?, ended_at = ?, summary = COALESCE(?, summary), payload_json = ?
            WHERE project_id = ? AND run_id = ? AND status = 'running'
            """,
            (
                normalized_status,
                normalized_ended_at,
                summary,
                payload_to_text(merged_payload),
                project_id,
                run_id,
            ),
        )
        updated = _get_run_row(connection, project_id, run_id)
        connection.commit()
        return _run_from_row(updated)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def append_event_with_artifacts(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
    event_uuid: str | None,
    ts: str,
    phase: str,
    level: str,
    tool: str,
    source: str,
    message: str,
    payload: dict[str, Any] | None,
    artifacts: EventArtifacts = EMPTY_EVENT_ARTIFACTS,
) -> dict[str, Any]:
    if not isinstance(artifacts, EventArtifacts):
        raise TypeError("artifacts must be an EventArtifacts value")
    if not all(isinstance(item, RawLogArtifact) for item in artifacts.raw_logs):
        raise TypeError("artifacts.raw_logs must contain only RawLogArtifact values")
    if not all(isinstance(item, ErrorArtifact) for item in artifacts.errors):
        raise TypeError("artifacts.errors must contain only ErrorArtifact values")
    canonical_uuid = normalize_event_uuid(event_uuid)
    normalized_ts = normalize_timestamp(ts)
    normalized_phase = normalize_phase(phase)
    normalized_level = normalize_level(level)
    connection = connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        run = _get_run_row(connection, project_id, run_id)
        if run is None:
            raise RunNotFoundError(f"No run {run_id} exists in project {project_id}")
        if run["status"] != "running":
            existing = get_event_by_uuid(connection, canonical_uuid)
            if existing is None:
                raise RunNotRunningError(
                    f"run {run_id} is {run['status']} and cannot accept a new event"
                )
        sequence_no = int(run["next_sequence_no"])
        event, inserted = insert_event(
            connection,
            event_uuid=canonical_uuid,
            project_id=project_id,
            run_id=run_id,
            sequence_no=sequence_no,
            ts=normalized_ts,
            phase=normalized_phase,
            level=normalized_level,
            tool=tool,
            source=source,
            message=message,
            payload=payload,
        )
        raw_reports: list[dict[str, Any]] = []
        error_reports: list[dict[str, Any]] = []
        try:
            for artifact in artifacts.raw_logs:
                raw_log_id = stable_raw_log_id(
                    project_id=project_id,
                    run_id=run_id,
                    kind=artifact.kind,
                    path=artifact.path,
                )
                record, artifact_inserted = insert_raw_log(
                    connection,
                    project_id=project_id,
                    raw_log_id=raw_log_id,
                    run_id=run_id,
                    kind=artifact.kind,
                    path=artifact.path,
                    created_at=normalized_ts,
                    sha256=artifact.sha256,
                )
                raw_reports.append(
                    {"record": record, "inserted": artifact_inserted}
                )
            for artifact in artifacts.errors:
                error_created_at = (
                    normalize_timestamp(artifact.created_at)
                    if artifact.created_at is not None
                    else normalized_ts
                )
                error_id = stable_error_id(
                    project_id=project_id,
                    run_id=run_id,
                    occurrence_key=artifact.occurrence_key,
                    error_kind=artifact.error_kind,
                    file=artifact.file,
                    line=artifact.line,
                    column=artifact.column,
                    exception_type=artifact.exception_type,
                    message=artifact.message,
                    raw_text=artifact.raw_text,
                )
                record, artifact_inserted = insert_error(
                    connection,
                    project_id=project_id,
                    error_id=error_id,
                    run_id=run_id,
                    error_kind=artifact.error_kind,
                    file=artifact.file,
                    line=artifact.line,
                    column=artifact.column,
                    exception_type=artifact.exception_type,
                    message=artifact.message,
                    raw_text=artifact.raw_text,
                    recoverable=artifact.recoverable,
                    created_at=error_created_at,
                )
                error_reports.append(
                    {"record": record, "inserted": artifact_inserted}
                )
        except (RawLogRepositoryError, ErrorRepositoryError, sqlite3.Error) as exc:
            raise ArtifactProjectionError(
                f"Could not project completion artifacts: {exc}"
            ) from exc
        if inserted:
            connection.execute(
                """
                UPDATE runs SET next_sequence_no = next_sequence_no + 1
                WHERE project_id = ? AND run_id = ?
                """,
                (project_id, run_id),
            )
        connection.commit()
        return {
            "event": event,
            "event_inserted": inserted,
            "raw_logs": raw_reports,
            "errors": error_reports,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def reconcile_existing_event_artifacts(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
    event_uuid: str,
    artifacts: EventArtifacts = EMPTY_EVENT_ARTIFACTS,
    expected_event_profile: dict[str, Any] | None = None,
    expected_run_profile: dict[str, Any] | None = None,
    expected_sequence_no: int | None = None,
    expected_next_sequence_no: int | None = None,
    expected_event_ts_tolerance_seconds: float = 0,
    raw_claims: tuple[HistoricalRawClaim, ...] = (),
) -> dict[str, Any]:
    """Atomically add evidence to an existing event on a terminal run.

    Historical reconciliation is intentionally existing-only: this entry point
    never creates an event, changes a run, or advances its sequence.
    """

    if not isinstance(artifacts, EventArtifacts):
        raise TypeError("artifacts must be an EventArtifacts value")
    if not all(isinstance(item, RawLogArtifact) for item in artifacts.raw_logs):
        raise TypeError("artifacts.raw_logs must contain only RawLogArtifact values")
    if not all(isinstance(item, ErrorArtifact) for item in artifacts.errors):
        raise TypeError("artifacts.errors must contain only ErrorArtifact values")
    if not isinstance(event_uuid, str) or not event_uuid.strip():
        raise LogRepositoryError("event_uuid is required")
    canonical_uuid = normalize_event_uuid(event_uuid)
    if (
        isinstance(expected_event_ts_tolerance_seconds, bool)
        or not isinstance(expected_event_ts_tolerance_seconds, (int, float))
        or not math.isfinite(float(expected_event_ts_tolerance_seconds))
        or not 0 <= float(expected_event_ts_tolerance_seconds) <= 1
    ):
        raise LogRepositoryError(
            "expected_event_ts_tolerance_seconds must be a finite number "
            "between 0 and 1"
        )
    event_ts_tolerance_seconds = float(
        expected_event_ts_tolerance_seconds
    )
    if not isinstance(raw_claims, tuple):
        raise TypeError("raw_claims must be a tuple")
    normalized_claims = tuple(
        _normalized_historical_claim(claim) for claim in raw_claims
    )
    for label, value in (
        ("expected_sequence_no", expected_sequence_no),
        ("expected_next_sequence_no", expected_next_sequence_no),
    ):
        if (
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            )
        ):
            raise LogRepositoryError(f"{label} must be a positive integer")
    _require_profile_shape(
        expected_event_profile,
        label="event",
        required_fields=_HISTORICAL_EVENT_PROFILE_FIELDS,
    )
    _require_profile_shape(
        expected_run_profile,
        label="run",
        required_fields=_HISTORICAL_RUN_PROFILE_FIELDS,
    )
    if normalized_claims:
        if (
            expected_event_profile is None
            or expected_run_profile is None
            or expected_sequence_no is None
            or expected_next_sequence_no is None
        ):
            raise LogRepositoryError(
                "historical raw claims require complete profile and sequence gates"
            )
        artifact_identities = [
            (
                normalize_raw_log_path(artifact.path),
                str(artifact.kind or "").strip(),
                normalize_sha256(artifact.sha256),
            )
            for artifact in artifacts.raw_logs
        ]
        claim_identities = [
            (claim["path"], claim["kind"], claim["sha256"])
            for claim in normalized_claims
        ]
        if (
            any(identity[2] is None for identity in artifact_identities)
            or len(set(identity[0] for identity in artifact_identities))
            != len(artifact_identities)
            or sorted(artifact_identities) != sorted(claim_identities)
        ):
            raise LogRepositoryError(
                "historical raw claims must exactly match the raw artifact bundle"
            )
        expected_profile_sha256 = canonical_profile_sha256(
            expected_event_profile
        )
        expected_bundle_sha256 = event_artifact_bundle_sha256(artifacts)
        if any(
            claim["event_profile_sha256"] != expected_profile_sha256
            or claim["artifact_bundle_sha256"] != expected_bundle_sha256
            for claim in normalized_claims
        ):
            raise LogRepositoryError(
                "historical raw claim digests do not bind the supplied profiles"
            )

    connection = connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        run = _get_run_row(connection, project_id, run_id)
        if run is None:
            raise RunNotFoundError(
                f"No run {run_id} exists in project {project_id}"
            )
        event = get_event_by_uuid(connection, canonical_uuid)
        if (
            event is None
            or event["project_id"] != project_id
            or event["run_id"] != run_id
        ):
            raise EventNotFoundError(
                f"No event {canonical_uuid} exists for run {run_id} "
                f"in project {project_id}"
            )
        run_record = {
            **_run_from_row(run),
            "terminal_event_uuid": canonical_uuid,
        }
        _require_profile_match(
            event,
            expected_event_profile,
            label="event",
            required_fields=_HISTORICAL_EVENT_PROFILE_FIELDS,
            event_ts_tolerance_seconds=event_ts_tolerance_seconds,
        )
        _require_profile_match(
            run_record,
            expected_run_profile,
            label="run",
            required_fields=_HISTORICAL_RUN_PROFILE_FIELDS,
        )
        if (
            expected_sequence_no is not None
            and int(event["sequence_no"]) != expected_sequence_no
        ):
            raise HistoricalArtifactProfileConflictError(
                "historical event sequence conflicts with its source profile"
            )
        if (
            expected_next_sequence_no is not None
            and int(run["next_sequence_no"]) != expected_next_sequence_no
        ):
            raise HistoricalArtifactProfileConflictError(
                "historical run next_sequence_no conflicts with its source profile"
            )
        if run["status"] == "running":
            raise RunNotTerminalError(
                f"run {run_id} is running and cannot reconcile historical artifacts"
            )
        if (
            event["phase"] != "complete"
            or int(event["sequence_no"]) != int(run["next_sequence_no"]) - 1
        ):
            raise EventNotTerminalError(
                f"event {canonical_uuid} is not the terminal completion event "
                f"for run {run_id}"
            )
        try:
            event_ts = normalize_timestamp(str(event["ts"]))
            claim_reports: list[dict[str, Any]] = []
            raw_reports: list[dict[str, Any]] = []
            error_reports: list[dict[str, Any]] = []
            for claim in normalized_claims:
                record, inserted = _insert_historical_raw_claim(
                    connection,
                    project_id=project_id,
                    run_id=run_id,
                    event_uuid=canonical_uuid,
                    claimed_at=event_ts,
                    claim=claim,
                )
                claim_reports.append(
                    {"record": record, "inserted": inserted}
                )
            for artifact in artifacts.raw_logs:
                raw_log_id = stable_raw_log_id(
                    project_id=project_id,
                    run_id=run_id,
                    kind=artifact.kind,
                    path=artifact.path,
                )
                record, inserted = insert_raw_log(
                    connection,
                    project_id=project_id,
                    raw_log_id=raw_log_id,
                    run_id=run_id,
                    kind=artifact.kind,
                    path=artifact.path,
                    created_at=event_ts,
                    sha256=artifact.sha256,
                )
                raw_reports.append({"record": record, "inserted": inserted})
            for artifact in artifacts.errors:
                error_created_at = (
                    normalize_timestamp(artifact.created_at)
                    if artifact.created_at is not None
                    else event_ts
                )
                error_id = stable_error_id(
                    project_id=project_id,
                    run_id=run_id,
                    occurrence_key=artifact.occurrence_key,
                    error_kind=artifact.error_kind,
                    file=artifact.file,
                    line=artifact.line,
                    column=artifact.column,
                    exception_type=artifact.exception_type,
                    message=artifact.message,
                    raw_text=artifact.raw_text,
                )
                record, inserted = insert_error(
                    connection,
                    project_id=project_id,
                    error_id=error_id,
                    run_id=run_id,
                    error_kind=artifact.error_kind,
                    file=artifact.file,
                    line=artifact.line,
                    column=artifact.column,
                    exception_type=artifact.exception_type,
                    message=artifact.message,
                    raw_text=artifact.raw_text,
                    recoverable=artifact.recoverable,
                    created_at=error_created_at,
                )
                error_reports.append({"record": record, "inserted": inserted})
            connection.commit()
        except HistoricalRawClaimConflictError:
            raise
        except (
            RawLogRepositoryError,
            ErrorRepositoryError,
            EventRepositoryError,
            sqlite3.Error,
        ) as exc:
            raise ArtifactProjectionError(
                f"Could not reconcile historical event artifacts: {exc}"
            ) from exc
        return {
            "event": event,
            "event_inserted": False,
            "raw_claims": claim_reports,
            "raw_logs": raw_reports,
            "errors": error_reports,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def finalize_existing_run_with_artifacts(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
    expected_task_type: str,
    status: str,
    ended_at: str,
    summary: str | None,
    run_payload: dict[str, Any] | None,
    event_uuid: str,
    ts: str,
    phase: str,
    level: str,
    tool: str,
    source: str,
    message: str,
    event_payload: dict[str, Any] | None,
    artifacts: EventArtifacts = EMPTY_EVENT_ARTIFACTS,
) -> dict[str, Any]:
    """Atomically project a terminal event, its evidence, and the run state.

    This entry point intentionally requires an existing run.  It is used by
    recovery code where filesystem metadata must never be allowed to create a
    new SQLite run.
    """

    if not isinstance(artifacts, EventArtifacts):
        raise TypeError("artifacts must be an EventArtifacts value")
    normalized_task_type = str(expected_task_type).strip()
    if not normalized_task_type:
        raise LogRepositoryError("expected_task_type is required")
    normalized_status = str(status).strip().lower()
    if normalized_status not in RUN_STATUSES - {"running"}:
        raise LogRepositoryError(
            "finished run status must be succeeded, failed, or cancelled"
        )
    canonical_uuid = normalize_event_uuid(event_uuid)
    normalized_ts = normalize_timestamp(ts)
    normalized_ended_at = normalize_timestamp(ended_at)
    normalized_phase = normalize_phase(phase)
    normalized_level = normalize_level(level)
    if run_payload is not None and not isinstance(run_payload, dict):
        raise LogRepositoryError("run payload_json must be a JSON object")

    connection = connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        run = _get_run_row(connection, project_id, run_id)
        if run is None:
            raise RunNotFoundError(
                f"No run {run_id} exists in project {project_id}"
            )
        if run["task_type"] != normalized_task_type:
            raise RunTaskTypeConflictError(
                f"run {run_id} has task_type {run['task_type']}, "
                f"expected {normalized_task_type}"
            )
        if run["status"] not in {"running", normalized_status}:
            raise RunStateConflictError(
                f"run {run_id} is already {run['status']} and cannot become "
                f"{normalized_status}"
            )
        existing_event = get_event_by_uuid(connection, canonical_uuid)
        if run["status"] != "running" and existing_event is None:
            raise RunNotRunningError(
                f"run {run_id} is {run['status']} and is missing its terminal event"
            )

        sequence_no = int(run["next_sequence_no"])
        event, inserted = insert_event(
            connection,
            event_uuid=canonical_uuid,
            project_id=project_id,
            run_id=run_id,
            sequence_no=sequence_no,
            ts=normalized_ts,
            phase=normalized_phase,
            level=normalized_level,
            tool=tool,
            source=source,
            message=message,
            payload=event_payload,
        )
        raw_reports: list[dict[str, Any]] = []
        error_reports: list[dict[str, Any]] = []
        try:
            for artifact in artifacts.raw_logs:
                raw_log_id = stable_raw_log_id(
                    project_id=project_id,
                    run_id=run_id,
                    kind=artifact.kind,
                    path=artifact.path,
                )
                record, artifact_inserted = insert_raw_log(
                    connection,
                    project_id=project_id,
                    raw_log_id=raw_log_id,
                    run_id=run_id,
                    kind=artifact.kind,
                    path=artifact.path,
                    created_at=normalized_ts,
                    sha256=artifact.sha256,
                )
                raw_reports.append(
                    {"record": record, "inserted": artifact_inserted}
                )
            for artifact in artifacts.errors:
                error_created_at = (
                    normalize_timestamp(artifact.created_at)
                    if artifact.created_at is not None
                    else normalized_ts
                )
                error_id = stable_error_id(
                    project_id=project_id,
                    run_id=run_id,
                    occurrence_key=artifact.occurrence_key,
                    error_kind=artifact.error_kind,
                    file=artifact.file,
                    line=artifact.line,
                    column=artifact.column,
                    exception_type=artifact.exception_type,
                    message=artifact.message,
                    raw_text=artifact.raw_text,
                )
                record, artifact_inserted = insert_error(
                    connection,
                    project_id=project_id,
                    error_id=error_id,
                    run_id=run_id,
                    error_kind=artifact.error_kind,
                    file=artifact.file,
                    line=artifact.line,
                    column=artifact.column,
                    exception_type=artifact.exception_type,
                    message=artifact.message,
                    raw_text=artifact.raw_text,
                    recoverable=artifact.recoverable,
                    created_at=error_created_at,
                )
                error_reports.append(
                    {"record": record, "inserted": artifact_inserted}
                )
        except (RawLogRepositoryError, ErrorRepositoryError, sqlite3.Error) as exc:
            raise ArtifactProjectionError(
                f"Could not project completion artifacts: {exc}"
            ) from exc
        expected_raw_ids = {
            report["record"]["raw_log_id"] for report in raw_reports
        }
        actual_raw_ids = {
            record["raw_log_id"]
            for record in list_raw_logs_for_run(
                connection,
                project_id=project_id,
                run_id=run_id,
            )
        }
        expected_error_ids = {
            report["record"]["error_id"] for report in error_reports
        }
        actual_error_ids = {
            record["error_id"]
            for record in list_errors_for_run(
                connection,
                project_id=project_id,
                run_id=run_id,
            )
        }
        if (
            actual_raw_ids != expected_raw_ids
            or actual_error_ids != expected_error_ids
        ):
            raise ArtifactProjectionError(
                "Persisted completion artifacts do not match the canonical bundle."
            )

        merged_payload = payload_from_text(run["payload_json"])
        if run_payload:
            merged_payload.update(run_payload)
        if run["status"] == "running":
            connection.execute(
                """
                UPDATE runs
                SET status = ?, ended_at = ?, summary = COALESCE(?, summary),
                    payload_json = ?,
                    next_sequence_no = next_sequence_no + ?
                WHERE project_id = ? AND run_id = ? AND status = 'running'
                """,
                (
                    normalized_status,
                    normalized_ended_at,
                    summary,
                    payload_to_text(merged_payload),
                    1 if inserted else 0,
                    project_id,
                    run_id,
                ),
            )
        else:
            current_payload = payload_from_text(run["payload_json"])
            payload_matches = all(
                current_payload.get(key) == value
                for key, value in (run_payload or {}).items()
            )
            if (
                run["ended_at"] != normalized_ended_at
                or (summary is not None and run["summary"] != summary)
                or not payload_matches
            ):
                raise RunStateConflictError(
                    f"run {run_id} terminal metadata conflicts with the "
                    "deterministic retry"
                )
            if inserted:
                raise RunStateConflictError(
                    f"run {run_id} accepted a new event after termination"
                )

        updated = _get_run_row(connection, project_id, run_id)
        connection.commit()
        return {
            "event": event,
            "event_inserted": inserted,
            "raw_logs": raw_reports,
            "errors": error_reports,
            "run": _run_from_row(updated),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def append_event(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
    event_uuid: str | None,
    ts: str,
    phase: str,
    level: str,
    tool: str,
    source: str,
    message: str,
    payload: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    report = append_event_with_artifacts(
        database,
        project_id=project_id,
        run_id=run_id,
        event_uuid=event_uuid,
        ts=ts,
        phase=phase,
        level=level,
        tool=tool,
        source=source,
        message=message,
        payload=payload,
    )
    return report["event"], report["event_inserted"]


def get_run_events(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
    tail: int = 80,
) -> list[dict[str, Any]]:
    connection = connect(database)
    try:
        return list_events_for_run(connection, project_id=project_id, run_id=run_id, tail=tail)
    finally:
        connection.close()


def register_raw_log(
    database: str | Path,
    *,
    project_id: str,
    raw_log_id: str,
    run_id: str,
    kind: str,
    path: str,
    created_at: str,
    sha256: str | None,
) -> tuple[dict[str, Any], bool]:
    connection = connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        record, inserted = insert_raw_log(
            connection,
            project_id=project_id,
            raw_log_id=raw_log_id,
            run_id=run_id,
            kind=kind,
            path=path,
            created_at=created_at,
            sha256=sha256,
        )
        connection.commit()
        return record, inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_raw_log(
    database: str | Path,
    *,
    project_id: str,
    raw_log_id: str,
) -> dict[str, Any] | None:
    connection = connect(database)
    try:
        return select_raw_log(
            connection,
            project_id=project_id,
            raw_log_id=raw_log_id,
        )
    finally:
        connection.close()


def get_run_raw_logs(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    connection = connect(database)
    try:
        return list_raw_logs_for_run(
            connection,
            project_id=project_id,
            run_id=run_id,
        )
    finally:
        connection.close()


def get_historical_raw_claim(
    database: str | Path,
    *,
    project_id: str,
    path: str,
) -> dict[str, Any] | None:
    normalized_path = normalize_raw_log_path(path)
    connection = connect(database)
    try:
        row = connection.execute(
            """
            SELECT * FROM historical_raw_claims
            WHERE project_id = ? AND path = ?
            """,
            (project_id, normalized_path),
        ).fetchone()
        return (
            _historical_claim_from_row(row)
            if row is not None
            else None
        )
    finally:
        connection.close()


def get_run_historical_raw_claims(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    connection = connect(database)
    try:
        rows = connection.execute(
            """
            SELECT * FROM historical_raw_claims
            WHERE project_id = ? AND run_id = ?
            ORDER BY path
            """,
            (project_id, run_id),
        ).fetchall()
        return [_historical_claim_from_row(row) for row in rows]
    finally:
        connection.close()


def register_error(
    database: str | Path,
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
    connection = connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        record, inserted = insert_error(
            connection,
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
        connection.commit()
        return record, inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_error(
    database: str | Path,
    *,
    project_id: str,
    error_id: str,
) -> dict[str, Any] | None:
    connection = connect(database)
    try:
        return select_error(
            connection,
            project_id=project_id,
            error_id=error_id,
        )
    finally:
        connection.close()


def get_run_errors(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    connection = connect(database)
    try:
        return list_errors_for_run(
            connection,
            project_id=project_id,
            run_id=run_id,
        )
    finally:
        connection.close()


def _latest_run_row(connection: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT r.* FROM runs AS r
        LEFT JOIN (
          SELECT project_id, run_id, MAX(ts) AS last_event_at
          FROM events
          WHERE project_id = ?
          GROUP BY project_id, run_id
        ) AS e
          ON e.project_id = r.project_id AND e.run_id = r.run_id
        WHERE r.project_id = ?
        ORDER BY MAX(
          r.started_at,
          COALESCE(r.ended_at, ''),
          COALESCE(e.last_event_at, '')
        ) DESC, r.run_id DESC
        LIMIT 1
        """,
        (project_id, project_id),
    ).fetchone()


def latest_run(database: str | Path, *, project_id: str) -> dict[str, Any] | None:
    connection = connect(database)
    try:
        row = _latest_run_row(connection, project_id)
        return _run_from_row(row) if row is not None else None
    finally:
        connection.close()


def read_latest_run_snapshot(database: str | Path, *, project_id: str) -> dict[str, Any]:
    connection, schema_version = _open_readonly_snapshot(database)
    try:
        row = _latest_run_row(connection, project_id)
        latest = _run_from_row(row) if row is not None else None
        last_event = None
        if latest is not None:
            events = list_events_for_run(
                connection,
                project_id=project_id,
                run_id=latest["run_id"],
                tail=1,
            )
            if events:
                last_event = events[-1]
        return {
            "schema_version": schema_version,
            "latest": latest,
            "last_event": last_event,
        }
    except sqlite3.DatabaseError as exc:
        raise _classify_sqlite_query_error(exc) from exc
    except (EventRepositoryError, KeyError, TypeError, ValueError) as exc:
        raise LogDatabaseInvalidError(
            "SQLite log database contains invalid stored log data."
        ) from exc
    finally:
        connection.close()


def read_run_snapshot(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
    tail: int,
    raw_log_limit: int = 1_000,
    error_limit: int = 200,
    error_file_char_limit: int = 4_096,
    error_exception_type_char_limit: int = 256,
    error_message_char_limit: int = 2_048,
    error_raw_text_char_limit: int = 8_192,
) -> dict[str, Any]:
    connection, schema_version = _open_readonly_snapshot(database)
    try:
        row = _get_run_row(connection, project_id, run_id)
        run = _run_from_row(row) if row is not None else None
        events = (
            list_events_for_run(
                connection,
                project_id=project_id,
                run_id=run_id,
                tail=tail,
            )
            if run is not None
            else []
        )
        raw_logs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        raw_logs_truncated = False
        errors_truncated = False
        error_fields_truncated = False
        if run is not None and schema_version == CURRENT_SCHEMA_VERSION:
            raw_logs, raw_logs_truncated = list_recent_raw_logs_for_run(
                connection,
                project_id=project_id,
                run_id=run_id,
                limit=raw_log_limit,
            )
            (
                errors,
                errors_truncated,
                error_fields_truncated,
            ) = list_recent_errors_for_run(
                connection,
                project_id=project_id,
                run_id=run_id,
                limit=error_limit,
                file_char_limit=error_file_char_limit,
                exception_type_char_limit=error_exception_type_char_limit,
                message_char_limit=error_message_char_limit,
                raw_text_char_limit=error_raw_text_char_limit,
            )
        return {
            "schema_version": schema_version,
            "run": run,
            "events": events,
            "raw_logs": raw_logs,
            "errors": errors,
            "raw_logs_truncated": raw_logs_truncated,
            "errors_truncated": errors_truncated,
            "error_fields_truncated": error_fields_truncated,
        }
    except sqlite3.DatabaseError as exc:
        raise _classify_sqlite_query_error(exc) from exc
    except (EventRepositoryError, KeyError, TypeError, ValueError) as exc:
        raise LogDatabaseInvalidError(
            "SQLite log database contains invalid stored log data."
        ) from exc
    finally:
        connection.close()


def _list_bounded_error_parse_events(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    run_id: str,
    limit: int,
    message_char_limit: int,
    payload_char_limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    limits = {
        "limit": limit,
        "message_char_limit": message_char_limit,
        "payload_char_limit": payload_char_limit,
    }
    for field, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise EventRepositoryError(f"{field} must be a positive integer")
    rows = connection.execute(
        """
        SELECT
          sequence_no,
          substr(message, 1, :message_probe_limit) AS message_probe,
          substr(payload_json, 1, :payload_probe_limit) AS payload_probe,
          length(message) > :message_char_limit AS message_truncated,
          length(payload_json) > :payload_char_limit AS payload_truncated
        FROM events
        WHERE project_id = :project_id AND run_id = :run_id
        ORDER BY sequence_no DESC
        LIMIT :row_limit
        """,
        {
            "project_id": project_id,
            "run_id": run_id,
            "row_limit": limit + 1,
            "message_char_limit": message_char_limit,
            "message_probe_limit": message_char_limit,
            "payload_char_limit": payload_char_limit,
            "payload_probe_limit": payload_char_limit,
        },
    ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows[:limit]:
        message = row["message_probe"]
        payload_text = row["payload_probe"]
        if not isinstance(message, str) or not isinstance(payload_text, str):
            raise EventRepositoryError(
                "stored event message and payload_json must be text"
            )
        payload_truncated = bool(row["payload_truncated"])
        payload: dict[str, Any] | None = None
        if not payload_truncated:
            decoded = json.loads(payload_text)
            if not isinstance(decoded, dict):
                raise EventRepositoryError(
                    "stored event payload_json must decode to an object"
                )
            payload = decoded
        records.append(
            {
                "sequence_no": int(row["sequence_no"]),
                "message": message,
                "payload_json": payload,
                "field_truncation": {
                    "message": bool(row["message_truncated"]),
                    "payload_json": payload_truncated,
                },
            }
        )
    records.reverse()
    return records, len(rows) > limit


def read_error_parse_snapshot(
    database: str | Path,
    *,
    project_id: str,
    run_id: str,
    raw_log_limit: int,
    error_limit: int,
    error_file_char_limit: int,
    error_exception_type_char_limit: int,
    error_message_char_limit: int,
    error_raw_text_char_limit: int,
    legacy_event_limit: int,
    legacy_message_char_limit: int,
    legacy_payload_char_limit: int,
) -> dict[str, Any]:
    """Read all C3 source selectors from one query-only SQLite snapshot.

    Compatibility events are projected through SQL-side character limits and
    a fixed row window. A truncated payload is never decoded or treated as a
    file path/error report.
    """

    connection, schema_version = _open_readonly_snapshot(database)
    try:
        row = _get_run_row(connection, project_id, run_id)
        run = _run_from_row(row) if row is not None else None
        raw_logs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        raw_logs_truncated = False
        errors_truncated = False
        error_fields_truncated = False
        legacy_events: list[dict[str, Any]] = []
        legacy_events_truncated = False
        if run is not None:
            if schema_version == CURRENT_SCHEMA_VERSION:
                raw_logs, raw_logs_truncated = list_recent_raw_logs_for_run(
                    connection,
                    project_id=project_id,
                    run_id=run_id,
                    limit=raw_log_limit,
                )
                (
                    errors,
                    errors_truncated,
                    error_fields_truncated,
                ) = list_recent_errors_for_run(
                    connection,
                    project_id=project_id,
                    run_id=run_id,
                    limit=error_limit,
                    file_char_limit=error_file_char_limit,
                    exception_type_char_limit=error_exception_type_char_limit,
                    message_char_limit=error_message_char_limit,
                    raw_text_char_limit=error_raw_text_char_limit,
                )
            legacy_events, legacy_events_truncated = (
                _list_bounded_error_parse_events(
                    connection,
                    project_id=project_id,
                    run_id=run_id,
                    limit=legacy_event_limit,
                    message_char_limit=legacy_message_char_limit,
                    payload_char_limit=legacy_payload_char_limit,
                )
            )
        return {
            "schema_version": schema_version,
            "run": run,
            "raw_logs": raw_logs,
            "errors": errors,
            "raw_logs_truncated": raw_logs_truncated,
            "errors_truncated": errors_truncated,
            "error_fields_truncated": error_fields_truncated,
            "legacy_events": legacy_events,
            "legacy_events_truncated": legacy_events_truncated,
        }
    except sqlite3.DatabaseError as exc:
        raise _classify_sqlite_query_error(exc) from exc
    except (
        ErrorRepositoryError,
        EventRepositoryError,
        RawLogRepositoryError,
        json.JSONDecodeError,
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:
        raise LogDatabaseInvalidError(
            "SQLite log database contains invalid stored error-parse data."
        ) from exc
    finally:
        connection.close()


def query_events(
    database: str | Path,
    *,
    project_id: str,
    terms: Iterable[str] = (),
    limit: int = 20,
    run_id: str | None = None,
    phase: str | None = None,
    level: str | None = None,
    tool: str | None = None,
    source: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    sequence_from: int | None = None,
    sequence_to: int | None = None,
) -> list[dict[str, Any]]:
    connection = connect(database)
    try:
        return select_events(
            connection,
            project_id=project_id,
            terms=terms,
            limit=limit,
            run_id=run_id,
            phase=phase,
            level=level,
            tool=tool,
            source=source,
            from_ts=from_ts,
            to_ts=to_ts,
            sequence_from=sequence_from,
            sequence_to=sequence_to,
        )
    finally:
        connection.close()


def query_events_readonly(
    database: str | Path,
    *,
    project_id: str,
    terms: Iterable[str] = (),
    limit: int = 20,
    run_id: str | None = None,
    phase: str | None = None,
    level: str | None = None,
    tool: str | None = None,
    source: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    sequence_from: int | None = None,
    sequence_to: int | None = None,
) -> dict[str, Any]:
    if (sequence_from is not None or sequence_to is not None) and not run_id:
        raise EventRepositoryError("run_id is required when filtering by sequence number")
    if sequence_from is not None and sequence_from < 1:
        raise EventRepositoryError("sequence_from must be at least 1")
    if sequence_to is not None and sequence_to < 1:
        raise EventRepositoryError("sequence_to must be at least 1")
    if sequence_from is not None and sequence_to is not None and sequence_from > sequence_to:
        raise EventRepositoryError("sequence_from must not exceed sequence_to")
    normalized_phase = normalize_phase(phase) if phase is not None else None
    normalized_level = normalize_level(level) if level is not None else None
    normalized_from_ts = normalize_timestamp(from_ts) if from_ts is not None else None
    normalized_to_ts = normalize_timestamp(to_ts) if to_ts is not None else None
    if (
        normalized_from_ts is not None
        and normalized_to_ts is not None
        and normalized_from_ts > normalized_to_ts
    ):
        raise EventRepositoryError("from_ts must not exceed to_ts")

    connection, schema_version = _open_readonly_snapshot(database)
    try:
        matches = select_events(
            connection,
            project_id=project_id,
            terms=terms,
            limit=limit,
            run_id=run_id,
            phase=normalized_phase,
            level=normalized_level,
            tool=tool,
            source=source,
            from_ts=normalized_from_ts,
            to_ts=normalized_to_ts,
            sequence_from=sequence_from,
            sequence_to=sequence_to,
        )
        return {"schema_version": schema_version, "matches": matches}
    except sqlite3.DatabaseError as exc:
        raise _classify_sqlite_query_error(exc) from exc
    except (EventRepositoryError, KeyError, TypeError, ValueError) as exc:
        raise LogDatabaseInvalidError(
            "SQLite log database contains invalid stored log data."
        ) from exc
    finally:
        connection.close()


def _read_jsonl_snapshot(source_path: Path) -> tuple[str, list[dict[str, Any]]]:
    content = source_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    lines = content.decode("utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                continue
            raise
        if not isinstance(record, dict):
            raise LogRepositoryError(f"JSONL record {index + 1} must be an object")
        records.append(record)
    return digest, records


def _legacy_timestamp(record: dict[str, Any]) -> str:
    try:
        return normalize_timestamp(str(record.get("ts") or ""))
    except EventRepositoryError:
        return "1970-01-01T00:00:00+00:00"


def legacy_jsonl_event_uuid(
    project_id: str,
    run_id: str,
    line_number: int,
    record: dict[str, Any],
) -> str:
    candidate = record.get("event_uuid")
    if candidate:
        try:
            return normalize_event_uuid(str(candidate))
        except EventRepositoryError:
            pass
    event_id = record.get("event_id")
    if event_id:
        identity = f"event:{project_id}:{run_id}:{event_id}"
    else:
        canonical_record = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        record_hash = hashlib.sha256(canonical_record.encode("utf-8")).hexdigest()
        identity = f"line:{project_id}:{run_id}:{line_number}:{record_hash}"
    return str(uuid5(LEGACY_JSONL_NAMESPACE, identity))


def import_jsonl_sessions(
    database: str | Path,
    *,
    project_id: str,
    logs_root: str | Path,
) -> dict[str, Any]:
    sessions_root = Path(logs_root) / "sessions"
    if not sessions_root.exists():
        return {"files_imported": 0, "events_imported": 0, "events_deduplicated": 0}
    files_imported = 0
    events_imported = 0
    events_deduplicated = 0
    for source_path in sorted(sessions_root.glob("*.jsonl")):
        digest, records = _read_jsonl_snapshot(source_path)
        source_key = str(source_path.resolve())
        marker_connection = connect(database)
        try:
            marker = marker_connection.execute(
                """
                SELECT 1 FROM legacy_jsonl_imports
                WHERE project_id = ? AND source_path = ? AND content_sha256 = ?
                """,
                (project_id, source_key, digest),
            ).fetchone()
        finally:
            marker_connection.close()
        if marker is not None:
            continue

        managed_runs: set[str] = set()
        run_errors: dict[str, bool] = {}
        run_last_ts: dict[str, str] = {}
        run_has_explicit_phase: dict[str, bool] = {}
        run_has_complete: dict[str, bool] = {}
        run_cancelled: dict[str, bool] = {}
        for line_number, record in enumerate(records, start=1):
            run_id = str(record.get("run_id") or source_path.stem)
            tool = str(record.get("tool") or "legacy_jsonl")
            task_type = str(record.get("task_type") or tool)
            timestamp = _legacy_timestamp(record)
            payload = record.get("payload_json")
            if not isinstance(payload, dict):
                payload = record.get("data") if isinstance(record.get("data"), dict) else {}
            payload = dict(payload)
            selected_port = record.get("selected_port")
            if not isinstance(selected_port, str) or not selected_port.strip():
                payload_port = payload.get("port")
                selected_port = payload_port if isinstance(payload_port, str) and payload_port.strip() else None
            legacy_event_uuid = legacy_jsonl_event_uuid(
                project_id,
                run_id,
                line_number,
                record,
            )
            run = get_run(database, project_id=project_id, run_id=run_id)
            native_run = run is not None and "legacy_jsonl_source" not in run["payload_json"]
            if native_run:
                event_connection = connect(database)
                try:
                    existing_event = get_event_by_uuid(event_connection, legacy_event_uuid)
                finally:
                    event_connection.close()
                if existing_event is None:
                    raise NativeRunImportConflictError(
                        f"JSONL cannot add a new event to native run {run_id}"
                    )
            if run is None:
                run, created = create_run(
                    database,
                    project_id=project_id,
                    run_id=run_id,
                    task_type=task_type,
                    started_at=timestamp,
                    selected_port=selected_port,
                    payload={"legacy_jsonl_source": source_key},
                )
                if created:
                    managed_runs.add(run_id)
            else:
                if selected_port is not None and not native_run:
                    run, _ = create_run(
                        database,
                        project_id=project_id,
                        run_id=run_id,
                        task_type=run["task_type"],
                        started_at=run["started_at"],
                        selected_port=selected_port,
                        payload=run["payload_json"],
                    )
                if not native_run and run["status"] == "running":
                    managed_runs.add(run_id)
            raw_phase_value = record.get("phase")
            raw_phase = str(raw_phase_value or "unknown").lower()
            try:
                phase = normalize_phase(raw_phase)
            except EventRepositoryError:
                phase = "unknown"
            raw_level = str(record.get("level") or "info")
            legacy_level: str | None = None
            try:
                level = normalize_level(raw_level)
            except EventRepositoryError:
                level = "info"
                legacy_level = raw_level
            if legacy_level is not None:
                payload.setdefault("legacy_level", legacy_level)
            event, inserted = append_event(
                database,
                project_id=project_id,
                run_id=run_id,
                event_uuid=legacy_event_uuid,
                ts=timestamp,
                phase=phase,
                level=level,
                tool=tool,
                source=str(record.get("source") or "legacy_jsonl"),
                message=str(record.get("message") or ""),
                payload=payload,
            )
            if inserted:
                events_imported += 1
            else:
                events_deduplicated += 1
            if run_id in managed_runs:
                run_errors[run_id] = run_errors.get(run_id, False) or event["level"] in {"error", "critical"}
                run_last_ts[run_id] = max(run_last_ts.get(run_id, timestamp), timestamp)
                explicit_phase = raw_phase_value is not None and phase != "unknown"
                run_has_explicit_phase[run_id] = run_has_explicit_phase.get(run_id, False) or explicit_phase
                run_has_complete[run_id] = run_has_complete.get(run_id, False) or phase == "complete"
                stopped = str(payload.get("state") or "").strip().upper() == "STOPPED"
                run_cancelled[run_id] = run_cancelled.get(run_id, False) or (
                    stopped and (raw_phase_value is None or phase == "complete")
                )

        for run_id in managed_runs:
            if run_id not in run_last_ts:
                continue
            if run_has_explicit_phase.get(run_id, False) and not run_has_complete.get(run_id, False):
                continue
            finish_run(
                database,
                project_id=project_id,
                run_id=run_id,
                status=(
                    "failed"
                    if run_errors.get(run_id, False)
                    else "cancelled"
                    if run_cancelled.get(run_id, False)
                    else "succeeded"
                ),
                ended_at=run_last_ts[run_id],
                summary=f"Imported from {source_path.name}",
            )

        marker_connection = connect(database)
        try:
            marker_connection.execute("BEGIN IMMEDIATE")
            cursor = marker_connection.execute(
                """
                INSERT OR IGNORE INTO legacy_jsonl_imports (
                  project_id, source_path, content_sha256, event_count, imported_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, source_key, digest, len(records), _now_iso()),
            )
            marker_connection.commit()
            files_imported += int(cursor.rowcount > 0)
        except Exception:
            marker_connection.rollback()
            raise
        finally:
            marker_connection.close()

    return {
        "files_imported": files_imported,
        "events_imported": events_imported,
        "events_deduplicated": events_deduplicated,
    }
