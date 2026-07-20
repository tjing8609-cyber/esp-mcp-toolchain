from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from uuid import UUID, uuid5

from .db import connect
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


RUN_STATUSES = {"running", "succeeded", "failed", "cancelled"}
LEGACY_JSONL_NAMESPACE = UUID("28446ce5-4840-4d6d-a354-187721231ff8")


class LogRepositoryError(RuntimeError):
    error_kind = "log_repository_error"


class RunNotFoundError(LogRepositoryError):
    error_kind = "run_not_found"


class RunConflictError(LogRepositoryError):
    error_kind = "run_id_conflict"


class RunNotRunningError(LogRepositoryError):
    error_kind = "run_not_running"


class RunStateConflictError(LogRepositoryError):
    error_kind = "run_state_conflict"


class NativeRunImportConflictError(RunConflictError):
    error_kind = "native_run_import_conflict"


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


def _get_run_row(connection: sqlite3.Connection, project_id: str, run_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM runs WHERE project_id = ? AND run_id = ?",
        (project_id, run_id),
    ).fetchone()


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
    connection = connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        run = _get_run_row(connection, project_id, run_id)
        if run is None:
            raise RunNotFoundError(f"No run {run_id} exists in project {project_id}")
        canonical_uuid = normalize_event_uuid(event_uuid) if event_uuid is not None else None
        if run["status"] != "running":
            existing = get_event_by_uuid(connection, canonical_uuid) if canonical_uuid is not None else None
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
            ts=ts,
            phase=phase,
            level=level,
            tool=tool,
            source=source,
            message=message,
            payload=payload,
        )
        if inserted:
            connection.execute(
                """
                UPDATE runs SET next_sequence_no = next_sequence_no + 1
                WHERE project_id = ? AND run_id = ?
                """,
                (project_id, run_id),
            )
        connection.commit()
        return event, inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

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


def latest_run(database: str | Path, *, project_id: str) -> dict[str, Any] | None:
    connection = connect(database)
    try:
        row = connection.execute(
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
        return _run_from_row(row) if row is not None else None
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


def _legacy_uuid(
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
            legacy_event_uuid = _legacy_uuid(project_id, run_id, line_number, record)
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