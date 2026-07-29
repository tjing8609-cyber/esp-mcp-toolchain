from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from . import historical_capture_adapter
from . import historical_reconciliation_store
from . import serial_monitor_backend
from . import serial_monitor_store
from ..database import log_repository
from ..database.migrations import CURRENT_SCHEMA_VERSION
from ..tools.log_tools import LogScope
from ..utils.time_utils import now_utc_iso


MAX_PROJECT_SESSION_FILES = 10_000
MAX_PROJECT_MONITOR_RUNS = 10_000
PROJECT_RECONCILIATION_VERSION = 1
PROJECT_RECONCILIATION_FORMAT = (
    "esp-mcp-toolchain.historical-sqlite-artifacts"
)
_CAPTURE_TASK_TYPES = {"serial_capture", "esp_serial_capture"}


class HistoricalArtifactReconciliationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_kind: str = "historical_artifact_reconciliation_failed",
        retryable: bool = False,
    ):
        super().__init__(message)
        self.error_kind = error_kind
        self.retryable = retryable


@dataclass(frozen=True)
class _DatabaseSnapshot:
    schema_version: int
    runs: dict[str, dict[str, Any]]
    events: dict[str, dict[str, Any]]
    last_events: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class _CaptureSpec:
    source_name: str
    run_id: str
    event_uuid: str


@dataclass(frozen=True)
class _MonitorSpec:
    run_id: str
    event_uuid: str


@dataclass(frozen=True)
class _ResolvedCandidate:
    kind: str
    run_id: str
    event_uuid: str
    identity: str
    candidate: Any
    fingerprint: str


def _error_kind(exc: BaseException) -> str:
    value = getattr(exc, "error_kind", None)
    return (
        str(value)
        if isinstance(value, str) and value
        else "historical_artifact_reconciliation_failed"
    )


def _retryable(exc: BaseException) -> bool:
    return bool(
        getattr(exc, "retryable", False)
        or getattr(exc, "recoverable", False)
    )


def _error_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "error_kind": _error_kind(exc),
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "retryable": _retryable(exc),
    }


def _validate_scope(scope: LogScope) -> None:
    if not isinstance(scope, LogScope):
        raise TypeError("scope must be a LogScope value")
    if not isinstance(scope.project_id, str) or not scope.project_id:
        raise HistoricalArtifactReconciliationError(
            "Historical reconciliation project_id is invalid.",
            error_kind="historical_project_scope_invalid",
        )
    project_dir = Path(scope.project_dir)
    log_root = Path(scope.log_root)
    database_file = Path(scope.database_file)
    if (
        not project_dir.is_absolute()
        or not log_root.is_absolute()
        or not database_file.is_absolute()
        or log_root.parent != project_dir
        or database_file.parent != project_dir
    ):
        raise HistoricalArtifactReconciliationError(
            "Historical reconciliation scope paths are not project-bound.",
            error_kind="historical_project_scope_invalid",
        )
    try:
        serial_monitor_store.safe_directory_identity(
            project_dir,
            label="Historical reconciliation project directory",
            include_metadata=False,
        )
    except Exception as exc:
        raise HistoricalArtifactReconciliationError(
            f"Historical reconciliation project directory is unsafe: {exc}",
            error_kind="historical_project_scope_invalid",
        ) from exc


def _readonly_connection(database_file: Path) -> sqlite3.Connection:
    try:
        resolved = database_file.resolve(strict=True)
    except OSError as exc:
        raise HistoricalArtifactReconciliationError(
            f"Historical SQLite database is unavailable: {exc}",
            error_kind="historical_schema_not_ready",
        ) from exc
    uri = f"{resolved.as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error as exc:
        raise HistoricalArtifactReconciliationError(
            f"Historical SQLite database could not be opened read-only: {exc}",
            error_kind="historical_schema_not_ready",
        ) from exc


def _payload_from_text(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise HistoricalArtifactReconciliationError(
            f"Historical SQLite payload is invalid: {exc}",
            error_kind="historical_database_snapshot_invalid",
        ) from exc
    if not isinstance(payload, dict):
        raise HistoricalArtifactReconciliationError(
            "Historical SQLite payload is not an object.",
            error_kind="historical_database_snapshot_invalid",
        )
    return payload


def _run_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["project_id"],
        "run_id": row["run_id"],
        "task_type": row["task_type"],
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "next_sequence_no": int(row["next_sequence_no"]),
        "selected_port": row["selected_port"],
        "summary": row["summary"],
        "payload_json": _payload_from_text(row["payload_json"]),
    }


def _event_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_uuid": row["event_uuid"],
        "project_id": row["project_id"],
        "run_id": row["run_id"],
        "sequence_no": int(row["sequence_no"]),
        "ts": row["ts"],
        "phase": row["phase"],
        "level": row["level"],
        "tool": row["tool"],
        "source": row["source"],
        "message": row["message"],
        "payload_json": _payload_from_text(row["payload_json"]),
    }


def _database_snapshot(scope: LogScope) -> _DatabaseSnapshot:
    connection = _readonly_connection(Path(scope.database_file))
    try:
        schema_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if schema_version != CURRENT_SCHEMA_VERSION:
            raise HistoricalArtifactReconciliationError(
                "Historical reconciliation requires schema "
                f"v{CURRENT_SCHEMA_VERSION}; found v{schema_version}.",
                error_kind="historical_schema_not_ready",
            )
        required_tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table'
                  AND name IN (
                    'runs', 'events', 'raw_logs', 'errors',
                    'historical_raw_claims'
                  )
                """
            )
        }
        if required_tables != {
            "runs",
            "events",
            "raw_logs",
            "errors",
            "historical_raw_claims",
        }:
            raise HistoricalArtifactReconciliationError(
                "Historical schema v3 tables are incomplete.",
                error_kind="historical_schema_not_ready",
            )
        runs = {
            record["run_id"]: record
            for record in (
                _run_record(row)
                for row in connection.execute(
                    """
                    SELECT * FROM runs
                    WHERE project_id = ?
                    ORDER BY run_id
                    """,
                    (scope.project_id,),
                )
            )
        }
        events: dict[str, dict[str, Any]] = {}
        last_events: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            """
            SELECT * FROM events
            WHERE project_id = ?
            ORDER BY run_id, sequence_no
            """,
            (scope.project_id,),
        ):
            record = _event_record(row)
            events[record["event_uuid"]] = record
            last_events[record["run_id"]] = record
        return _DatabaseSnapshot(
            schema_version=schema_version,
            runs=runs,
            events=events,
            last_events=last_events,
        )
    finally:
        connection.close()


def _schema_version_without_mutation(scope: LogScope) -> int | None:
    try:
        connection = _readonly_connection(Path(scope.database_file))
    except HistoricalArtifactReconciliationError:
        return None
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def _direct_children(
    root: Path,
    *,
    want_directories: bool,
    maximum: int,
    label: str,
) -> list[Path]:
    try:
        root.lstat()
    except FileNotFoundError:
        return []
    before = serial_monitor_store.safe_directory_identity(
        root,
        label=label,
        include_metadata=False,
    )
    selected: list[Path] = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise HistoricalArtifactReconciliationError(
            f"{label} could not be enumerated: {exc}",
            error_kind="historical_project_scan_failed",
        ) from exc
    if len(children) > maximum:
        raise HistoricalArtifactReconciliationError(
            f"{label} exceeds the supported entry count.",
            error_kind="historical_project_scan_limit_exceeded",
        )
    for child in children:
        try:
            status = child.lstat()
        except OSError as exc:
            raise HistoricalArtifactReconciliationError(
                f"{label} entry is unavailable: {child.name}: {exc}",
                error_kind="historical_project_scan_failed",
            ) from exc
        if child.is_symlink() or bool(
            getattr(status, "st_file_attributes", 0) & 0x400
        ):
            raise HistoricalArtifactReconciliationError(
                f"{label} entry is a link or reparse point: {child.name}",
                error_kind="historical_project_scan_failed",
            )
        if want_directories and child.is_dir():
            selected.append(child)
        elif not want_directories and child.is_file():
            selected.append(child)
    after = serial_monitor_store.safe_directory_identity(
        root,
        label=label,
        include_metadata=False,
    )
    if after != before:
        raise HistoricalArtifactReconciliationError(
            f"{label} changed during enumeration.",
            error_kind="historical_project_scan_changed",
            retryable=True,
        )
    return selected


def _inspect_capture_source(
    scope: LogScope,
    path: Path,
) -> _CaptureSpec | None:
    if path.suffix.lower() != ".jsonl":
        return None
    source_name = path.name
    records: list[tuple[int, dict[str, Any]]] = []
    total = 0
    try:
        with serial_monitor_store._safe_binary_reader(
            path,
            parent=path.parent,
            label="Historical capture session",
        ) as (handle, status):
            if int(status.st_size) > historical_capture_adapter.MAX_SOURCE_BYTES:
                raise HistoricalArtifactReconciliationError(
                    f"Historical session {source_name} exceeds the size limit.",
                    error_kind="historical_capture_scan_failed",
                )
            raw = handle.read(
                historical_capture_adapter.MAX_SOURCE_BYTES + 1
            )
        if len(raw) > historical_capture_adapter.MAX_SOURCE_BYTES:
            raise HistoricalArtifactReconciliationError(
                f"Historical session {source_name} exceeds the size limit.",
                error_kind="historical_capture_scan_failed",
            )
        text = raw.decode("utf-8", errors="strict")
        for number, line in enumerate(text.splitlines(), start=1):
            if not line:
                continue
            if len(line.encode("utf-8")) > (
                historical_capture_adapter.MAX_SOURCE_LINE_BYTES
            ):
                raise ValueError("line exceeds the supported size")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("record is not an object")
            total += 1
            if total > historical_capture_adapter.MAX_SOURCE_RECORDS:
                raise ValueError("record count exceeds the supported limit")
            records.append((number, value))
    except HistoricalArtifactReconciliationError:
        raise
    except Exception as exc:
        raise HistoricalArtifactReconciliationError(
            f"Historical session {source_name} could not be inspected: "
            f"{type(exc).__name__}: {exc}",
            error_kind="historical_capture_scan_failed",
        ) from exc
    capture_records = [
        (number, record)
        for number, record in records
        if record.get("tool") == "esp_serial_capture"
    ]
    if not capture_records:
        return None
    run_ids = {
        str(record.get("run_id") or "")
        for _number, record in capture_records
    }
    if len(run_ids) != 1 or not next(iter(run_ids)):
        raise HistoricalArtifactReconciliationError(
            f"Historical capture session {source_name} has conflicting run IDs.",
            error_kind="historical_capture_scan_failed",
        )
    run_id = next(iter(run_ids))
    number, selected = capture_records[-1]
    event_uuid = log_repository.legacy_jsonl_event_uuid(
        scope.project_id,
        run_id,
        number,
        selected,
    )
    return _CaptureSpec(
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )


def _capture_specs(scope: LogScope) -> list[_CaptureSpec]:
    sessions_root = Path(scope.log_root) / "sessions"
    paths = _direct_children(
        sessions_root,
        want_directories=False,
        maximum=MAX_PROJECT_SESSION_FILES,
        label="Historical sessions directory",
    )
    specs = [
        spec
        for path in paths
        if (spec := _inspect_capture_source(scope, path)) is not None
    ]
    return sorted(
        specs,
        key=lambda spec: (spec.run_id, spec.source_name, spec.event_uuid),
    )


def _monitor_specs(
    scope: LogScope,
    snapshot: _DatabaseSnapshot,
) -> tuple[list[_MonitorSpec], list[dict[str, Any]]]:
    serial_root = Path(scope.log_root) / "serial"
    paths = _direct_children(
        serial_root,
        want_directories=True,
        maximum=MAX_PROJECT_MONITOR_RUNS,
        label="Historical monitor directory",
    )
    specs: list[_MonitorSpec] = []
    items: list[dict[str, Any]] = []
    for path in paths:
        run_id = path.name
        run = snapshot.runs.get(run_id)
        event = snapshot.last_events.get(run_id)
        if run is None or run.get("task_type") != "serial_monitor":
            items.append(
                {
                    "kind": "monitor",
                    "identity": f"serial/{run_id}",
                    "run_id": run_id,
                    "event_uuid": None,
                    "status": "ineligible",
                    "reason": "database_run_missing",
                    "database_persisted": False,
                }
            )
            continue
        if event is None:
            items.append(
                {
                    "kind": "monitor",
                    "identity": f"serial/{run_id}",
                    "run_id": run_id,
                    "event_uuid": None,
                    "status": "ineligible",
                    "reason": "database_event_missing",
                    "database_persisted": False,
                }
            )
            continue
        specs.append(
            _MonitorSpec(
                run_id=run_id,
                event_uuid=str(event["event_uuid"]),
            )
        )
    return specs, items


def _artifact_descriptor(
    artifacts: log_repository.EventArtifacts,
) -> dict[str, Any]:
    return {
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


def _candidate_descriptor(
    kind: str,
    candidate: Any,
) -> dict[str, Any]:
    common = {
        "kind": kind,
        "adapter_id": candidate.adapter_id,
        "reconciliation_version": candidate.reconciliation_version,
        "project_id": candidate.project_id,
        "run_id": candidate.run_id,
        "event_uuid": candidate.requested_event_uuid,
        "status": candidate.status,
        "event_profile": candidate.expected_event_profile,
        "run_profile": candidate.expected_run_profile,
        "expected_event_profile_sha256": (
            candidate.expected_event_profile_sha256
        ),
        "artifact_bundle_sha256": candidate.artifact_bundle_sha256,
        "artifacts": _artifact_descriptor(candidate.artifacts),
    }
    if kind == "capture":
        common.update(
            {
                "source_path": candidate.source_path,
                "source_sha256": candidate.source_sha256,
                "source_record_sha256": candidate.source_record_sha256,
                "source_format": candidate.source_format,
                "database_projection_eligible": (
                    candidate.database_projection_eligible
                ),
                "database_projection_reason": (
                    candidate.database_projection_reason
                ),
            }
        )
    else:
        common.update(
            {
                "manifest_format_version": (
                    candidate.manifest_format_version
                ),
                "manifest_sha256": candidate.manifest_sha256,
                "state": candidate.state,
                "terminal_at": candidate.terminal_at,
                "event_ts_tolerance_seconds": (
                    candidate.event_ts_tolerance_seconds
                ),
            }
        )
    return common


def _resolved_capture(
    scope: LogScope,
    spec: _CaptureSpec,
) -> _ResolvedCandidate:
    candidate = (
        historical_capture_adapter
        .resolve_historical_serial_capture_artifacts(
            scope,
            source_name=spec.source_name,
            run_id=spec.run_id,
            event_uuid=spec.event_uuid,
        )
    )
    descriptor = _candidate_descriptor("capture", candidate)
    return _ResolvedCandidate(
        kind="capture",
        run_id=spec.run_id,
        event_uuid=spec.event_uuid,
        identity=f"sessions/{spec.source_name}",
        candidate=candidate,
        fingerprint=log_repository.canonical_profile_sha256(descriptor),
    )


def _resolved_monitor(
    scope: LogScope,
    spec: _MonitorSpec,
) -> _ResolvedCandidate:
    candidate = serial_monitor_backend.resolve_historical_monitor_artifacts(
        scope,
        run_id=spec.run_id,
        event_uuid=spec.event_uuid,
    )
    descriptor = _candidate_descriptor("monitor", candidate)
    return _ResolvedCandidate(
        kind="monitor",
        run_id=spec.run_id,
        event_uuid=spec.event_uuid,
        identity=f"serial/{spec.run_id}/manifest.json",
        candidate=candidate,
        fingerprint=log_repository.canonical_profile_sha256(descriptor),
    )


def _raw_ambiguity(
    candidates: list[_ResolvedCandidate],
) -> dict[str, list[dict[str, Any]]]:
    owners: dict[str, list[dict[str, Any]]] = {}
    for resolved in candidates:
        for artifact in resolved.candidate.artifacts.raw_logs:
            owners.setdefault(artifact.path, []).append(
                {
                    "kind": resolved.kind,
                    "identity": resolved.identity,
                    "run_id": resolved.run_id,
                    "event_uuid": resolved.event_uuid,
                    "artifact_kind": artifact.kind,
                    "sha256": artifact.sha256,
                }
            )
    return {
        path: entries
        for path, entries in owners.items()
        if len(
            {
                (entry["run_id"], entry["event_uuid"])
                for entry in entries
            }
        )
        > 1
    }


def _candidate_item(
    resolved: _ResolvedCandidate,
    *,
    status: str,
    reason: str | None = None,
    database_persisted: bool = False,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "kind": resolved.kind,
        "identity": resolved.identity,
        "run_id": resolved.run_id,
        "event_uuid": resolved.event_uuid,
        "status": status,
        "reason": reason,
        "database_persisted": database_persisted,
        "adapter_id": resolved.candidate.adapter_id,
        "reconciliation_version": (
            resolved.candidate.reconciliation_version
        ),
        "candidate_fingerprint": resolved.fingerprint,
    }
    if error is not None:
        item["error"] = error
    return item


def _claims_for_candidate(candidate: Any) -> tuple[Any, ...]:
    return tuple(
        log_repository.HistoricalRawClaim(
            path=artifact.path,
            kind=artifact.kind,
            sha256=str(artifact.sha256),
            adapter_id=candidate.adapter_id,
            reconciliation_version=candidate.reconciliation_version,
            event_profile_sha256=(
                candidate.expected_event_profile_sha256
            ),
            artifact_bundle_sha256=candidate.artifact_bundle_sha256,
        )
        for artifact in candidate.artifacts.raw_logs
    )


def _database_binding(
    snapshot: _DatabaseSnapshot,
    resolved: _ResolvedCandidate,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run = snapshot.runs.get(resolved.run_id)
    event = snapshot.events.get(resolved.event_uuid)
    if run is None:
        raise HistoricalArtifactReconciliationError(
            f"No SQLite run exists for {resolved.run_id}.",
            error_kind="historical_database_run_missing",
        )
    if (
        event is None
        or event.get("run_id") != resolved.run_id
        or event.get("project_id") != run.get("project_id")
    ):
        raise HistoricalArtifactReconciliationError(
            f"No bound SQLite event exists for {resolved.identity}.",
            error_kind="historical_database_event_missing",
        )
    return run, event


def _apply_candidate(
    scope: LogScope,
    snapshot: _DatabaseSnapshot,
    resolved: _ResolvedCandidate,
) -> tuple[dict[str, Any], bool]:
    candidate = resolved.candidate
    if resolved.kind == "capture" and not (
        candidate.database_projection_eligible
    ):
        return (
            _candidate_item(
                resolved,
                status="ineligible",
                reason=candidate.database_projection_reason,
            ),
            False,
        )
    if candidate.status == "ineligible":
        return (
            _candidate_item(
                resolved,
                status="ineligible",
                reason=getattr(
                    candidate,
                    "database_projection_reason",
                    "candidate_ineligible",
                ),
            ),
            False,
        )
    if candidate.status == "no_artifacts":
        return (
            _candidate_item(
                resolved,
                status="no_artifacts",
                reason="candidate_has_no_artifacts",
            ),
            False,
        )
    run, event = _database_binding(snapshot, resolved)
    report = log_repository.reconcile_existing_event_artifacts(
        scope.database_file,
        project_id=scope.project_id,
        run_id=resolved.run_id,
        event_uuid=resolved.event_uuid,
        artifacts=candidate.artifacts,
        expected_event_profile=candidate.expected_event_profile,
        expected_run_profile=candidate.expected_run_profile,
        expected_sequence_no=int(event["sequence_no"]),
        expected_next_sequence_no=int(run["next_sequence_no"]),
        expected_event_ts_tolerance_seconds=float(
            getattr(candidate, "event_ts_tolerance_seconds", 0.0)
        ),
        raw_claims=_claims_for_candidate(candidate),
    )
    changed = any(
        bool(entry["inserted"])
        for collection in ("raw_claims", "raw_logs", "errors")
        for entry in report[collection]
    )
    return (
        _candidate_item(
            resolved,
            status="reconciled" if changed else "already_reconciled",
            database_persisted=changed,
        ),
        changed,
    )


def _empty_counts() -> dict[str, int]:
    return {
        "scanned": 0,
        "capture": 0,
        "monitor": 0,
        "reconciled": 0,
        "already_reconciled": 0,
        "ineligible": 0,
        "no_artifacts": 0,
        "failed": 0,
        "busy": 0,
        "ambiguous": 0,
    }


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = _empty_counts()
    counts["scanned"] = len(items)
    for item in items:
        kind = item.get("kind")
        if kind in {"capture", "monitor"}:
            counts[str(kind)] += 1
        status = item.get("status")
        if status in counts:
            counts[str(status)] += 1
    return counts


def _reconciliation_id(items: list[dict[str, Any]]) -> str:
    stable = [
        {
            key: item.get(key)
            for key in (
                "kind",
                "identity",
                "run_id",
                "event_uuid",
                "status",
                "reason",
                "candidate_fingerprint",
            )
        }
        for item in items
    ]
    return log_repository.canonical_profile_sha256({"items": stable})


def _marker_document(
    scope: LogScope,
    *,
    state: str,
    started_at: str,
    completed_at: str | None,
    database_persisted: bool,
    items: list[dict[str, Any]],
    error: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "format": PROJECT_RECONCILIATION_FORMAT,
        "version": PROJECT_RECONCILIATION_VERSION,
        "project_id": scope.project_id,
        "state": state,
        "started_at": started_at,
        "completed_at": completed_at,
        "database_persisted": database_persisted,
        "reconciliation_id": _reconciliation_id(items),
        "counts": _counts(items),
        "items": json.loads(json.dumps(items)),
        "error": json.loads(json.dumps(error)) if error else None,
    }


def _base_report(
    scope: LogScope,
    *,
    schema_version: int | None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "project_id": getattr(scope, "project_id", None),
        "state": "failed",
        "database_schema_version": schema_version,
        "database_persisted": False,
        "marker_persisted": False,
        "retryable": False,
        "counts": _empty_counts(),
        "items": [],
    }


def _failure_before_lease(
    scope: LogScope,
    *,
    schema_version: int | None,
    exc: BaseException,
) -> dict[str, Any]:
    report = _base_report(scope, schema_version=schema_version)
    report.update(
        {
            "error_kind": _error_kind(exc),
            "message": str(exc),
            "retryable": _retryable(exc),
        }
    )
    return report


def _publish_final(
    scope: LogScope,
    lease: Any,
    report: dict[str, Any],
    *,
    started_at: str,
    error: dict[str, Any] | None,
) -> dict[str, Any]:
    document = _marker_document(
        scope,
        state=str(report["state"]),
        started_at=started_at,
        completed_at=now_utc_iso(),
        database_persisted=bool(report["database_persisted"]),
        items=report["items"],
        error=error,
    )
    try:
        marker = (
            historical_reconciliation_store
            .publish_historical_reconciliation_marker(
                scope,
                lease,
                document,
            )
        )
        report["marker_persisted"] = True
        report["marker"] = marker
        return report
    except Exception as exc:
        report.update(
            {
                "ok": False,
                "state": "failed",
                "marker_persisted": False,
                "error_kind": "historical_marker_publish_failed",
                "message": (
                    "SQLite reconciliation finished, but the project marker "
                    f"could not be published: {type(exc).__name__}: {exc}"
                ),
                "retryable": True,
            }
        )
        return report


def _abort_with_marker(
    scope: LogScope,
    lease: Any,
    report: dict[str, Any],
    *,
    started_at: str,
    exc: BaseException,
) -> dict[str, Any]:
    error = _error_payload(exc)
    report.update(
        {
            "ok": False,
            "state": "failed",
            "error_kind": error["error_kind"],
            "message": error["message"],
            "retryable": error["retryable"],
            "counts": _counts(report["items"]),
        }
    )
    return _publish_final(
        scope,
        lease,
        report,
        started_at=started_at,
        error=error,
    )


def reconcile_historical_project_artifacts(
    scope: LogScope,
) -> dict[str, Any]:
    """Reconcile existing historical evidence without creating runs or events."""

    try:
        _validate_scope(scope)
    except Exception as exc:
        return _failure_before_lease(
            scope,
            schema_version=None,
            exc=exc,
        )
    schema_version = _schema_version_without_mutation(scope)
    if schema_version != CURRENT_SCHEMA_VERSION:
        exc = HistoricalArtifactReconciliationError(
            "Historical reconciliation is disabled until the project "
            f"database is explicitly upgraded to schema v{CURRENT_SCHEMA_VERSION}.",
            error_kind="historical_schema_not_ready",
        )
        return _failure_before_lease(
            scope,
            schema_version=schema_version,
            exc=exc,
        )

    report = _base_report(scope, schema_version=schema_version)
    started_at = now_utc_iso()
    lease = None
    try:
        lease = (
            historical_reconciliation_store
            .HistoricalProjectReconciliationLease.acquire(scope)
        )
    except Exception as exc:
        return _failure_before_lease(
            scope,
            schema_version=schema_version,
            exc=exc,
        )

    try:
        try:
            snapshot = _database_snapshot(scope)
            capture_specs = _capture_specs(scope)
            monitor_specs, initial_items = _monitor_specs(scope, snapshot)
            report["items"].extend(initial_items)

            captures = [
                _resolved_capture(scope, spec) for spec in capture_specs
            ]
            monitors: list[_ResolvedCandidate] = []
            for spec in monitor_specs:
                run_dir = Path(scope.log_root) / "serial" / spec.run_id
                run_lease = None
                try:
                    run_lease = (
                        serial_monitor_store
                        .SerialRunReconciliationLease.acquire(run_dir)
                    )
                    monitors.append(_resolved_monitor(scope, spec))
                except serial_monitor_store.SerialLogReconciliationBusy as exc:
                    raise HistoricalArtifactReconciliationError(
                        f"Historical monitor {spec.run_id} is busy.",
                        error_kind="historical_monitor_busy",
                        retryable=True,
                    ) from exc
                finally:
                    if run_lease is not None:
                        run_lease.release()
            first_pass = captures + monitors
            ambiguity = _raw_ambiguity(first_pass)
            if ambiguity:
                for path, owners in ambiguity.items():
                    report["items"].append(
                        {
                            "kind": "raw_path",
                            "identity": path,
                            "run_id": None,
                            "event_uuid": None,
                            "status": "ambiguous",
                            "reason": "different_event_owners",
                            "database_persisted": False,
                            "owners": owners,
                        }
                    )
                raise HistoricalArtifactReconciliationError(
                    "Historical raw paths have multiple event owners.",
                    error_kind="historical_raw_path_ambiguous",
                )

            running_document = _marker_document(
                scope,
                state="running",
                started_at=started_at,
                completed_at=None,
                database_persisted=False,
                items=report["items"],
                error=None,
            )
            (
                historical_reconciliation_store
                .publish_historical_reconciliation_marker(
                    scope,
                    lease,
                    running_document,
                )
            )

            refreshed_captures = [
                _resolved_capture(scope, spec) for spec in capture_specs
            ]
            for original, refreshed in zip(
                captures,
                refreshed_captures,
                strict=True,
            ):
                if original.fingerprint != refreshed.fingerprint:
                    raise HistoricalArtifactReconciliationError(
                        f"Historical candidate changed: {original.identity}",
                        error_kind="historical_candidate_changed",
                        retryable=True,
                    )
            refreshed_ambiguity = _raw_ambiguity(
                refreshed_captures + monitors
            )
            if refreshed_ambiguity:
                raise HistoricalArtifactReconciliationError(
                    "Historical raw ownership changed during verification.",
                    error_kind="historical_raw_path_ambiguous",
                    retryable=True,
                )

            database_changed = False
            for refreshed in refreshed_captures:
                try:
                    item, changed = _apply_candidate(
                        scope,
                        snapshot,
                        refreshed,
                    )
                except Exception as exc:
                    item = _candidate_item(
                        refreshed,
                        status="failed",
                        error=_error_payload(exc),
                    )
                    report["items"].append(item)
                    raise
                report["items"].append(item)
                database_changed = database_changed or changed

            monitor_by_run = {
                candidate.run_id: candidate for candidate in monitors
            }
            for spec in monitor_specs:
                original = monitor_by_run[spec.run_id]
                run_dir = Path(scope.log_root) / "serial" / spec.run_id
                run_lease = None
                try:
                    run_lease = (
                        serial_monitor_store
                        .SerialRunReconciliationLease.acquire(run_dir)
                    )
                    refreshed = _resolved_monitor(scope, spec)
                    if original.fingerprint != refreshed.fingerprint:
                        raise HistoricalArtifactReconciliationError(
                            "Historical monitor candidate changed: "
                            f"{original.identity}",
                            error_kind="historical_candidate_changed",
                            retryable=True,
                        )
                    item, changed = _apply_candidate(
                        scope,
                        snapshot,
                        refreshed,
                    )
                    report["items"].append(item)
                    database_changed = database_changed or changed
                except serial_monitor_store.SerialLogReconciliationBusy as exc:
                    raise HistoricalArtifactReconciliationError(
                        f"Historical monitor {spec.run_id} is busy.",
                        error_kind="historical_monitor_busy",
                        retryable=True,
                    ) from exc
                finally:
                    if run_lease is not None:
                        run_lease.release()

            report.update(
                {
                    "ok": True,
                    "state": "completed",
                    "database_persisted": database_changed,
                    "retryable": False,
                    "counts": _counts(report["items"]),
                }
            )
            return _publish_final(
                scope,
                lease,
                report,
                started_at=started_at,
                error=None,
            )
        except Exception as exc:
            report["database_persisted"] = any(
                bool(item.get("database_persisted"))
                for item in report["items"]
            )
            return _abort_with_marker(
                scope,
                lease,
                report,
                started_at=started_at,
                exc=exc,
            )
    finally:
        lease.release()


def read_historical_project_reconciliation_status(
    scope: LogScope,
) -> dict[str, Any]:
    """Read project reconciliation state without creating files or SQLite."""

    try:
        _validate_scope(scope)
        probe = (
            historical_reconciliation_store
            .probe_historical_reconciliation_lease(scope)
        )
        if isinstance(probe, dict):
            active = probe.get("active")
            active_error = probe.get("error")
            metadata_error = probe.get("metadata_error")
            owner = probe.get("owner")
        else:
            active = bool(probe)
            active_error = None
            metadata_error = None
            owner = None
        marker = (
            historical_reconciliation_store
            .load_historical_reconciliation_marker(scope)
        )
        if active is True:
            effective_state = "active"
        elif (
            marker is not None
            and marker.get("state") == "running"
            and active is False
        ):
            effective_state = "interrupted"
        elif marker is not None:
            effective_state = str(marker.get("state") or "unknown")
        elif active is False:
            effective_state = "idle"
        else:
            effective_state = "unknown"
        return {
            "ok": active_error is None and metadata_error is None,
            "project_id": scope.project_id,
            "active": active,
            "active_error": active_error,
            "metadata_error": metadata_error,
            "owner": owner,
            "marker": marker,
            "effective_state": effective_state,
        }
    except Exception as exc:
        return {
            "ok": False,
            "project_id": getattr(scope, "project_id", None),
            "active": None,
            "active_error": _error_payload(exc),
            "metadata_error": None,
            "owner": None,
            "marker": None,
            "effective_state": "unknown",
        }
