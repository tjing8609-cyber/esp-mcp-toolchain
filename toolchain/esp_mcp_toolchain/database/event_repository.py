from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Iterable
from uuid import RFC_4122, UUID, uuid4


PHASES = {"unknown", "prepare", "execute", "verify", "cleanup", "complete"}
LEVELS = {"debug", "info", "warning", "error", "critical"}
_LEVEL_ALIASES = {"warn": "warning", "fatal": "critical", "serial": "info"}


class EventRepositoryError(ValueError):
    error_kind = "event_repository_error"


class InvalidEventError(EventRepositoryError):
    error_kind = "invalid_log_event"


class EventUUIDConflictError(EventRepositoryError):
    error_kind = "event_uuid_conflict"


def normalize_phase(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in PHASES:
        raise InvalidEventError(f"phase must be one of {sorted(PHASES)}")
    return normalized


def normalize_level(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = _LEVEL_ALIASES.get(normalized, normalized)
    if normalized not in LEVELS:
        raise InvalidEventError(f"level must be one of {sorted(LEVELS)}")
    return normalized


def normalize_event_uuid(value: str | None) -> str:
    if value is None:
        return str(uuid4())
    raw = str(value)
    try:
        parsed = UUID(raw)
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidEventError("event_uuid must be a canonical RFC 4122 UUID") from exc
    canonical = str(parsed)
    if raw != canonical or parsed.version is None or parsed.variant != RFC_4122:
        raise InvalidEventError("event_uuid must be a canonical RFC 4122 UUID")
    return canonical


def normalize_timestamp(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidEventError("ts must be an ISO 8601 timestamp with a timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidEventError("ts must be an ISO 8601 timestamp with a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def payload_to_text(payload: dict[str, Any] | None) -> str:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise InvalidEventError("payload_json must be a JSON object")
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise InvalidEventError("payload_json must contain JSON-serializable values") from exc


def payload_from_text(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise InvalidEventError("stored payload_json is not a JSON object")
    return parsed


def event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = payload_from_text(row["payload_json"])
    return {
        "event_uuid": row["event_uuid"],
        "event_id": row["event_uuid"],
        "project_id": row["project_id"],
        "run_id": row["run_id"],
        "sequence_no": row["sequence_no"],
        "ts": row["ts"],
        "phase": row["phase"],
        "level": row["level"],
        "tool": row["tool"],
        "source": row["source"],
        "message": row["message"],
        "payload_json": payload,
        "data": payload,
    }


def _identity(
    *,
    project_id: str,
    run_id: str,
    ts: str,
    phase: str,
    level: str,
    tool: str,
    source: str,
    message: str,
    payload_text: str,
) -> tuple[Any, ...]:
    return (project_id, run_id, ts, phase, level, tool, source, message, payload_text)


def get_event_by_uuid(connection: sqlite3.Connection, event_uuid: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM events WHERE event_uuid = ?", (event_uuid,)).fetchone()
    return event_from_row(row) if row is not None else None


def insert_event(
    connection: sqlite3.Connection,
    *,
    event_uuid: str | None,
    project_id: str,
    run_id: str,
    sequence_no: int,
    ts: str,
    phase: str,
    level: str,
    tool: str,
    source: str,
    message: str,
    payload: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    canonical_uuid = normalize_event_uuid(event_uuid)
    normalized_ts = normalize_timestamp(ts)
    normalized_phase = normalize_phase(phase)
    normalized_level = normalize_level(level)
    if sequence_no < 1:
        raise InvalidEventError("sequence_no must be at least 1")
    if not str(project_id).strip() or not str(run_id).strip():
        raise InvalidEventError("project_id and run_id are required")
    if not str(tool).strip() or not str(source).strip():
        raise InvalidEventError("tool and source are required")
    payload_text = payload_to_text(payload)
    expected_identity = _identity(
        project_id=project_id,
        run_id=run_id,
        ts=normalized_ts,
        phase=normalized_phase,
        level=normalized_level,
        tool=tool,
        source=source,
        message=message,
        payload_text=payload_text,
    )
    existing_row = connection.execute("SELECT * FROM events WHERE event_uuid = ?", (canonical_uuid,)).fetchone()
    if existing_row is not None:
        existing_identity = _identity(
            project_id=existing_row["project_id"],
            run_id=existing_row["run_id"],
            ts=existing_row["ts"],
            phase=existing_row["phase"],
            level=existing_row["level"],
            tool=existing_row["tool"],
            source=existing_row["source"],
            message=existing_row["message"],
            payload_text=existing_row["payload_json"],
        )
        if existing_identity != expected_identity:
            raise EventUUIDConflictError(f"event_uuid {canonical_uuid} already identifies different content")
        return event_from_row(existing_row), False

    connection.execute(
        """
        INSERT INTO events (
          event_uuid, project_id, run_id, sequence_no, ts, phase, level,
          tool, source, message, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            canonical_uuid,
            project_id,
            run_id,
            sequence_no,
            normalized_ts,
            normalized_phase,
            normalized_level,
            tool,
            source,
            message,
            payload_text,
        ),
    )
    row = connection.execute("SELECT * FROM events WHERE event_uuid = ?", (canonical_uuid,)).fetchone()
    return event_from_row(row), True


def list_events_for_run(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    run_id: str,
    tail: int = 80,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM events
        WHERE project_id = ? AND run_id = ?
        ORDER BY sequence_no DESC
        LIMIT ?
        """,
        (project_id, run_id, max(0, tail)),
    ).fetchall()
    return [event_from_row(row) for row in reversed(rows)]


def query_events(
    connection: sqlite3.Connection,
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
    if (sequence_from is not None or sequence_to is not None) and not run_id:
        raise InvalidEventError("run_id is required when filtering by sequence number")
    if sequence_from is not None and sequence_from < 1:
        raise InvalidEventError("sequence_from must be at least 1")
    if sequence_to is not None and sequence_to < 1:
        raise InvalidEventError("sequence_to must be at least 1")
    if sequence_from is not None and sequence_to is not None and sequence_from > sequence_to:
        raise InvalidEventError("sequence_from must not exceed sequence_to")
    normalized_from_ts = normalize_timestamp(from_ts) if from_ts is not None else None
    normalized_to_ts = normalize_timestamp(to_ts) if to_ts is not None else None
    if normalized_from_ts is not None and normalized_to_ts is not None and normalized_from_ts > normalized_to_ts:
        raise InvalidEventError("from_ts must not exceed to_ts")

    clauses = ["project_id = ?"]
    parameters: list[Any] = [project_id]
    exact_filters = {
        "run_id": run_id,
        "phase": normalize_phase(phase) if phase is not None else None,
        "level": normalize_level(level) if level is not None else None,
        "tool": tool,
        "source": source,
    }
    for column, value in exact_filters.items():
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    if normalized_from_ts is not None:
        clauses.append("ts >= ?")
        parameters.append(normalized_from_ts)
    if normalized_to_ts is not None:
        clauses.append("ts <= ?")
        parameters.append(normalized_to_ts)
    if sequence_from is not None:
        clauses.append("sequence_no >= ?")
        parameters.append(sequence_from)
    if sequence_to is not None:
        clauses.append("sequence_no <= ?")
        parameters.append(sequence_to)
    searchable = "lower(tool || ' ' || source || ' ' || message || ' ' || payload_json)"
    for term in terms:
        clauses.append(f"{searchable} LIKE ? ESCAPE '\\'")
        escaped = str(term).lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parameters.append(f"%{escaped}%")
    parameters.append(max(0, limit))
    rows = connection.execute(
        f"""
        SELECT * FROM events
        WHERE {' AND '.join(clauses)}
        ORDER BY ts DESC, run_id DESC, sequence_no DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    return [event_from_row(row) for row in rows]
