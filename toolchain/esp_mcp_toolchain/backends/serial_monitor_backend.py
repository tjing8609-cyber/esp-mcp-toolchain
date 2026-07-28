from __future__ import annotations

import base64
import codecs
from collections import deque
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .pyserial_backend import (
    deactivate_and_close_serial,
    open_serial_with_inactive_control_lines,
)
from .serial_monitor_lock import PortLease, PortLockError, current_process_owner, identity_key
from .serial_monitor_store import (
    MAX_RECORD_BYTES,
    SQLITE_ARTIFACT_RECONCILIATION_VERSION,
    SerialLogQuotaError,
    SerialLogReconciliationBusy,
    SerialLogStore,
    SerialLogStoreError,
    SerialRunReconciliationLease,
    freeze_serial_run_first_runtime_error,
    freeze_serial_run_terminal_marker,
    load_manifest,
    load_serial_run_artifact_marker,
    mark_serial_run_audit_mirror,
    mark_serial_run_artifacts_reconciled,
    read_persisted_records,
    record_serial_run_artifact_reconciliation_error,
    recover_serial_runs,
    verified_finalized_chunks,
)
from ..database import log_repository
from ..database.event_repository import normalize_timestamp
from ..tools.log_tools import (
    LogScope,
    committed_event_and_latest_mirrors_match,
    mirror_committed_event_and_refresh_latest,
    write_event,
)
from ..utils.error_detection import MicroPythonErrorDetector
from ..utils.time_utils import now_utc_iso


DEFAULT_BUFFER_BYTES = 1024 * 1024
TERMINAL_STATES = {"STOPPED", "FAILED", "DISCONNECTED"}
_UTF8_MAX_PENDING_BYTES = 3
_INPUT_SLICE_BYTES = MAX_RECORD_BYTES - _UTF8_MAX_PENDING_BYTES
_SERIAL_READ_MAX_BYTES = 1024
_SERIAL_IDLE_SLEEP_SECONDS = 0.005
_TERMINAL_MARKER_VERSION = 1


class MonitorConflictError(RuntimeError):
    def __init__(self, error_kind: str, message: str):
        super().__init__(message)
        self.error_kind = error_kind


class SerialLogWriteFailure(RuntimeError):
    def __init__(self, cause: BaseException):
        super().__init__(str(cause))
        self.cause = cause


class MonitorState(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"


_TRANSITIONS = {
    MonitorState.STARTING: {MonitorState.RUNNING, MonitorState.STOPPING, MonitorState.FAILED},
    MonitorState.RUNNING: {MonitorState.STOPPING, MonitorState.DISCONNECTED, MonitorState.FAILED},
    MonitorState.STOPPING: {MonitorState.STOPPED, MonitorState.FAILED},
    MonitorState.STOPPED: set(),
    MonitorState.FAILED: set(),
    MonitorState.DISCONNECTED: set(),
}


@dataclass(frozen=True)
class MonitorBinding:
    run_id: str
    project_id: str
    project_dir: Path
    log_root: Path
    session_name: str
    port: str
    port_identity: dict
    baudrate: int


@dataclass(frozen=True)
class SerialRecord:
    seq: int
    timestamp_utc: str
    raw: bytes
    decode_error: bool


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _error_payload(error_kind: str, exc: BaseException) -> dict:
    return {
        "error_kind": error_kind,
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "timestamp_utc": now_utc_iso(),
    }


def _looks_disconnected(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "device disconnected",
            "device not connected",
            "device not found",
            "file not found",
            "no such device",
            "clearcommerror",
            "input/output error",
            "i/o error",
        )
    )


def _open_serial(serial_module: Any, binding: MonitorBinding) -> Any:
    return open_serial_with_inactive_control_lines(
        serial_module,
        binding.port,
        baudrate=binding.baudrate,
        timeout=0,
        write_timeout=1.0,
    )


def _read_serial_chunk(serial_port: Any) -> bytes:
    try:
        waiting = int(serial_port.in_waiting)
    except AttributeError:
        return bytes(serial_port.read(_SERIAL_READ_MAX_BYTES))
    if waiting <= 0:
        time.sleep(_SERIAL_IDLE_SLEEP_SECONDS)
        return b""
    return bytes(serial_port.read(min(waiting, _SERIAL_READ_MAX_BYTES)))


def _terminal_event_uuid(project_id: str, run_id: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            (
                "esp-mcp-toolchain:serial-monitor-terminal:"
                f"v{_TERMINAL_MARKER_VERSION}:{project_id}:{run_id}"
            ),
        )
    )


def _legacy_stale_event_uuid(project_id: str, run_id: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"esp-mcp-toolchain:stale-monitor:{project_id}:{run_id}",
        )
    )


def _resolve_legacy_stale_projection(
    binding: MonitorBinding,
    manifest: dict,
    log_scope: LogScope,
) -> dict | None:
    last_error = manifest.get("last_error")
    if (
        str(manifest.get("state") or "") != MonitorState.FAILED.value
        or not isinstance(last_error, dict)
        or last_error.get("error_kind") != "stale_monitor_recovered"
    ):
        return None
    run = log_repository.get_run(
        log_scope.database_file,
        project_id=binding.project_id,
        run_id=binding.run_id,
    )
    if run is None or run.get("status") == "running":
        return None
    current_uuid = _terminal_event_uuid(binding.project_id, binding.run_id)
    current_event = log_repository.get_event(
        log_scope.database_file,
        event_uuid=current_uuid,
    )
    if current_event is not None:
        if (
            current_event.get("project_id") != binding.project_id
            or current_event.get("run_id") != binding.run_id
        ):
            raise SerialLogStoreError(
                "Current monitor terminal event identity conflicts."
            )
        return None
    legacy_uuid = _legacy_stale_event_uuid(
        binding.project_id,
        binding.run_id,
    )
    legacy_event = log_repository.get_event(
        log_scope.database_file,
        event_uuid=legacy_uuid,
    )
    if legacy_event is None:
        raise SerialLogStoreError(
            "Failed legacy monitor run has no supported terminal event."
        )
    message = str(
        last_error.get("message")
        or "A previous monitor process ended without completing cleanup."
    )
    stopped_at = manifest.get("stopped_at")
    if not isinstance(stopped_at, str) or not stopped_at:
        raise SerialLogStoreError(
            "Legacy monitor manifest has no terminal timestamp."
        )
    expected_payload = {
        "state": MonitorState.FAILED.value,
        "last_error": last_error,
    }
    expected_event = {
        "event_uuid": legacy_uuid,
        "project_id": binding.project_id,
        "run_id": binding.run_id,
        "ts": normalize_timestamp(stopped_at),
        "phase": "complete",
        "level": "error",
        "tool": "esp_serial_monitor",
        "source": "monitor_recovery",
        "message": message,
        "payload_json": expected_payload,
    }
    if any(
        legacy_event.get(key) != value
        for key, value in expected_event.items()
    ):
        raise SerialLogStoreError(
            "Legacy monitor terminal event conflicts with historical facts."
        )
    latest_events = log_repository.get_run_events(
        log_scope.database_file,
        project_id=binding.project_id,
        run_id=binding.run_id,
        tail=1,
    )
    if (
        legacy_event.get("sequence_no") != int(run["next_sequence_no"]) - 1
        or len(latest_events) != 1
        or latest_events[0].get("event_uuid") != legacy_uuid
    ):
        raise SerialLogStoreError(
            "Legacy monitor terminal event is not the run's last event."
        )
    run_payload = run.get("payload_json")
    if (
        run.get("task_type") != "serial_monitor"
        or run.get("status") != "failed"
        or not isinstance(run.get("ended_at"), str)
        or not run.get("ended_at")
        or run.get("summary") != message
        or not isinstance(run_payload, dict)
        or run_payload.get("state") != MonitorState.FAILED.value
        or run_payload.get("last_error") != last_error
    ):
        raise SerialLogStoreError(
            "Legacy monitor run conflicts with historical terminal facts."
        )
    return {
        "event_origin": "legacy_08bce0b_stale_uuid",
        "canonical_v1_event_uuid": current_uuid,
        "event": legacy_event,
        "run": run,
    }


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _error_artifact(
    report: dict,
    *,
    occurrence_key: str,
    default_kind: str,
    created_at: str | None = None,
) -> log_repository.ErrorArtifact:
    error_kind = report.get("error_kind")
    return log_repository.ErrorArtifact(
        occurrence_key=occurrence_key,
        error_kind=(
            str(error_kind)
            if isinstance(error_kind, str) and error_kind
            else default_kind
        ),
        file=str(report["file"]) if isinstance(report.get("file"), str) else None,
        line=_optional_int(report.get("line")),
        column=_optional_int(report.get("column")),
        exception_type=(
            str(report["exception_type"])
            if isinstance(report.get("exception_type"), str)
            else None
        ),
        message=(
            str(report["message"])
            if isinstance(report.get("message"), str)
            else None
        ),
        raw_text=(
            str(report["raw_text"])
            if isinstance(report.get("raw_text"), str)
            else None
        ),
        recoverable=(
            report.get("recoverable")
            if isinstance(report.get("recoverable"), (bool, int))
            else None
        ),
        created_at=created_at,
    )


def _build_terminal_marker(
    binding: MonitorBinding,
    manifest: dict,
    *,
    artifact_marker: dict | None,
    event_uuid: str | None = None,
    event_origin: str | None = None,
) -> dict:
    run_id = str(manifest.get("run_id") or "")
    project_id = str(manifest.get("project_id") or "")
    if run_id != binding.run_id or project_id != binding.project_id:
        raise SerialLogStoreError(
            "Monitor manifest identity does not match its bound project and run."
        )
    state = str(manifest.get("state") or "").upper()
    if state not in TERMINAL_STATES:
        raise SerialLogStoreError(
            "Monitor manifest is not in a terminal state."
        )
    terminal_at = manifest.get("stopped_at")
    if not isinstance(terminal_at, str) or not terminal_at:
        raise SerialLogStoreError("Monitor manifest has no terminal timestamp.")
    last_error = (
        manifest.get("last_error")
        if isinstance(manifest.get("last_error"), dict)
        else None
    )
    first_runtime_error = (
        artifact_marker.get("first_runtime_error")
        if isinstance(artifact_marker, dict)
        and isinstance(artifact_marker.get("first_runtime_error"), dict)
        else None
    )
    frozen_report = (
        first_runtime_error.get("report")
        if isinstance(first_runtime_error, dict)
        and isinstance(first_runtime_error.get("report"), dict)
        else None
    )
    detected_error = (
        frozen_report
        if isinstance(frozen_report, dict)
        else (
            manifest.get("detected_error")
            if isinstance(manifest.get("detected_error"), dict)
            else None
        )
    )
    detected_error_at = (
        str(first_runtime_error.get("detected_at"))
        if isinstance(first_runtime_error, dict)
        and isinstance(first_runtime_error.get("detected_at"), str)
        and first_runtime_error.get("detected_at")
        else None
    )
    stale = bool(
        last_error
        and last_error.get("error_kind") == "stale_monitor_recovered"
    )
    run_status = "cancelled" if state == MonitorState.STOPPED.value else "failed"
    frozen_message = manifest.get("terminal_message")
    if isinstance(frozen_message, str) and frozen_message:
        message = frozen_message
    elif state == MonitorState.STOPPED.value:
        message = "Serial monitor stopped."
    elif last_error and isinstance(last_error.get("message"), str):
        message = str(last_error["message"])
    else:
        message = "Serial monitor ended with an error."
    has_detected_error = bool(
        detected_error and detected_error.get("has_error")
    )
    level = (
        "error"
        if run_status == "failed" or last_error or has_detected_error
        else "info"
    )
    selected_event_uuid = event_uuid or _terminal_event_uuid(project_id, run_id)
    marker = {
        "version": _TERMINAL_MARKER_VERSION,
        "marker_id": selected_event_uuid,
        "event_uuid": selected_event_uuid,
        "project_id": project_id,
        "run_id": run_id,
        "state": state,
        "run_status": run_status,
        "terminal_at": terminal_at,
        "level": level,
        "tool": "esp_serial_monitor",
        "source": "monitor_recovery" if stale else "serial_monitor_terminal",
        "message": message,
        "last_error": last_error,
        "detected_error": detected_error,
        "detected_error_at": detected_error_at,
        "stale_recovery": stale,
    }
    if event_origin is not None:
        marker["event_origin"] = event_origin
        marker["canonical_v1_event_uuid"] = _terminal_event_uuid(
            project_id,
            run_id,
        )
    return marker


def _validated_terminal_marker(
    binding: MonitorBinding,
    marker: dict,
    *,
    canonical: dict,
) -> dict:
    required = {
        "event_uuid",
        "project_id",
        "run_id",
        "state",
        "run_status",
        "terminal_at",
        "level",
        "tool",
        "source",
        "message",
        "detected_error_at",
    }
    if marker.get("version") != _TERMINAL_MARKER_VERSION:
        raise SerialLogStoreError("Monitor terminal marker version is unsupported.")
    if not required.issubset(marker):
        raise SerialLogStoreError("Monitor terminal marker is incomplete.")
    if (
        marker.get("project_id") != binding.project_id
        or marker.get("run_id") != binding.run_id
    ):
        raise SerialLogStoreError("Monitor terminal marker identity conflicts.")
    if (
        marker.get("event_uuid") != canonical.get("event_uuid")
        or marker.get("marker_id") != marker.get("event_uuid")
    ):
        raise SerialLogStoreError("Monitor terminal event UUID is not deterministic.")
    state = str(marker.get("state") or "")
    expected_status = "cancelled" if state == "STOPPED" else "failed"
    if state not in TERMINAL_STATES or marker.get("run_status") != expected_status:
        raise SerialLogStoreError("Monitor terminal marker state is invalid.")
    if marker != canonical:
        raise SerialLogStoreError(
            "Monitor terminal marker conflicts with canonical terminal facts."
        )
    return marker


def _terminal_artifacts(
    binding: MonitorBinding,
    manifest: dict,
    marker: dict,
) -> tuple[
    tuple[log_repository.RawLogArtifact, ...],
    tuple[log_repository.ErrorArtifact, ...],
    str,
]:
    verified_chunks = verified_finalized_chunks(binding.log_root, manifest)
    event_uuid = str(marker["event_uuid"])
    raw_logs = tuple(
        log_repository.RawLogArtifact(
            kind="serial_monitor_chunk",
            path=str(chunk["path"]),
            sha256=str(chunk["sha256"]),
        )
        for chunk in verified_chunks
    )
    errors: list[log_repository.ErrorArtifact] = []
    last_error = marker.get("last_error")
    if isinstance(last_error, dict):
        errors.append(
            _error_artifact(
                last_error,
                occurrence_key=f"event:{event_uuid}:last_error",
                default_kind="serial_monitor_failed",
            )
        )
    detected_error = marker.get("detected_error")
    if isinstance(detected_error, dict) and detected_error.get("has_error"):
        errors.append(
            _error_artifact(
                detected_error,
                occurrence_key=f"event:{event_uuid}:detected_error",
                default_kind="micropython_runtime_error",
                created_at=(
                    str(marker["detected_error_at"])
                    if marker.get("detected_error_at")
                    else None
                ),
            )
        )
    artifact_bundle = {
        "raw_logs": [
            {
                "kind": artifact.kind,
                "path": artifact.path,
                "sha256": artifact.sha256,
            }
            for artifact in raw_logs
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
            for artifact in errors
        ],
    }
    artifact_bundle_sha256 = hashlib.sha256(
        json.dumps(
            artifact_bundle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return raw_logs, tuple(errors), artifact_bundle_sha256


def _validated_committed_terminal_projection(
    binding: MonitorBinding,
    manifest: dict,
    artifact_marker: dict,
) -> tuple[dict, LogScope]:
    log_scope = LogScope.bound(
        project_id=binding.project_id,
        log_root=binding.log_root,
    )
    legacy_projection = _resolve_legacy_stale_projection(
        binding,
        manifest,
        log_scope,
    )
    selected_event_uuid = (
        str(legacy_projection["event"]["event_uuid"])
        if legacy_projection is not None
        else _terminal_event_uuid(binding.project_id, binding.run_id)
    )
    canonical_marker = _build_terminal_marker(
        binding,
        manifest,
        artifact_marker=artifact_marker,
        event_uuid=selected_event_uuid,
        event_origin=(
            str(legacy_projection["event_origin"])
            if legacy_projection is not None
            else None
        ),
    )
    terminal_marker = artifact_marker.get("terminal_marker")
    if not isinstance(terminal_marker, dict):
        raise SerialLogStoreError(
            "Committed monitor projection has no terminal marker."
        )
    marker = _validated_terminal_marker(
        binding,
        terminal_marker,
        canonical=canonical_marker,
    )
    projection = artifact_marker.get("projection")
    if (
        not isinstance(projection, dict)
        or projection.get("state") != "committed"
        or projection.get("event_uuid") != selected_event_uuid
    ):
        raise SerialLogStoreError(
            "Committed monitor projection identity conflicts."
        )
    raw_logs, errors, artifact_bundle_sha256 = _terminal_artifacts(
        binding,
        manifest,
        marker,
    )
    if legacy_projection is None:
        run = log_repository.get_run(
            log_scope.database_file,
            project_id=binding.project_id,
            run_id=binding.run_id,
        )
        event = log_repository.get_event(
            log_scope.database_file,
            event_uuid=selected_event_uuid,
        )
        expected_event = {
            "event_uuid": selected_event_uuid,
            "project_id": binding.project_id,
            "run_id": binding.run_id,
            "ts": normalize_timestamp(str(marker["terminal_at"])),
            "phase": "complete",
            "level": str(marker["level"]),
            "tool": str(marker["tool"]),
            "source": str(marker["source"]),
            "message": str(marker["message"]),
            "payload_json": {
                "state": marker["state"],
                "last_error": marker.get("last_error"),
                "detected_error": marker.get("detected_error"),
                "detected_error_at": marker.get("detected_error_at"),
                "terminal_marker_id": marker["marker_id"],
                "artifact_bundle_sha256": artifact_bundle_sha256,
            },
        }
        if event is None or any(
            event.get(key) != value for key, value in expected_event.items()
        ):
            raise SerialLogStoreError(
                "Committed monitor terminal event is missing or conflicts."
            )
        run_payload = run.get("payload_json") if isinstance(run, dict) else None
        expected_run_payload = {
            "state": marker["state"],
            "last_error": marker.get("last_error"),
            "detected_error": marker.get("detected_error"),
            "monitor_terminal_event_uuid": selected_event_uuid,
            "monitor_artifact_bundle_sha256": artifact_bundle_sha256,
        }
        if (
            run is None
            or run.get("task_type") != "serial_monitor"
            or run.get("status") != marker["run_status"]
            or run.get("ended_at")
            != normalize_timestamp(str(marker["terminal_at"]))
            or run.get("summary") != marker["message"]
            or not isinstance(run_payload, dict)
            or any(
                run_payload.get(key) != value
                for key, value in expected_run_payload.items()
            )
        ):
            raise SerialLogStoreError(
                "Committed monitor run is missing or conflicts."
            )
        latest_events = log_repository.get_run_events(
            log_scope.database_file,
            project_id=binding.project_id,
            run_id=binding.run_id,
            tail=1,
        )
        if (
            event.get("sequence_no") != int(run["next_sequence_no"]) - 1
            or len(latest_events) != 1
            or latest_events[0].get("event_uuid") != selected_event_uuid
        ):
            raise SerialLogStoreError(
                "Committed monitor terminal event is not the run's last event."
            )
    else:
        run = legacy_projection["run"]
        event = legacy_projection["event"]

    expected_raw: dict[str, dict] = {}
    for artifact in raw_logs:
        raw_log_id = log_repository.stable_raw_log_id(
            project_id=binding.project_id,
            run_id=binding.run_id,
            kind=artifact.kind,
            path=artifact.path,
        )
        expected_raw[raw_log_id] = {
            "kind": artifact.kind,
            "path": artifact.path,
            "sha256": artifact.sha256,
            "created_at": normalize_timestamp(str(event["ts"])),
        }
    actual_raw = {
        record["raw_log_id"]: record
        for record in log_repository.get_run_raw_logs(
            log_scope.database_file,
            project_id=binding.project_id,
            run_id=binding.run_id,
        )
    }
    if set(actual_raw) != set(expected_raw) or any(
        any(actual_raw[record_id].get(key) != value for key, value in expected.items())
        for record_id, expected in expected_raw.items()
    ):
        raise SerialLogStoreError(
            "Committed monitor raw artifact bundle is incomplete or conflicts."
        )

    expected_errors: dict[str, dict] = {}
    for artifact in errors:
        error_id = log_repository.stable_error_id(
            project_id=binding.project_id,
            run_id=binding.run_id,
            occurrence_key=artifact.occurrence_key,
            error_kind=artifact.error_kind,
            file=artifact.file,
            line=artifact.line,
            column=artifact.column,
            exception_type=artifact.exception_type,
            message=artifact.message,
            raw_text=artifact.raw_text,
        )
        expected_errors[error_id] = {
            "error_kind": artifact.error_kind,
            "file": artifact.file,
            "line": artifact.line,
            "column": artifact.column,
            "exception_type": artifact.exception_type,
            "message": artifact.message,
            "raw_text": artifact.raw_text,
            "recoverable": (
                None
                if artifact.recoverable is None
                else bool(artifact.recoverable)
            ),
            "created_at": normalize_timestamp(
                str(artifact.created_at or event["ts"])
            ),
        }
    actual_errors = {
        record["error_id"]: record
        for record in log_repository.get_run_errors(
            log_scope.database_file,
            project_id=binding.project_id,
            run_id=binding.run_id,
        )
    }
    if set(actual_errors) != set(expected_errors) or any(
        any(
            actual_errors[record_id].get(key) != value
            for key, value in expected.items()
        )
        for record_id, expected in expected_errors.items()
    ):
        raise SerialLogStoreError(
            "Committed monitor error artifact bundle is incomplete or conflicts."
        )
    return event, log_scope


def _reconcile_terminal_manifest(
    binding: MonitorBinding,
    manifest: dict,
    *,
    borrowed_lease: SerialRunReconciliationLease | None = None,
) -> dict:
    run_id = binding.run_id
    artifact_marker: dict | None = None
    active_lease = borrowed_lease
    owns_lease = active_lease is None
    try:
        run_dir = binding.log_root / "serial" / run_id
        if active_lease is None:
            active_lease = SerialRunReconciliationLease.acquire(run_dir)
        elif (
            not active_lease.held
            or active_lease.path.parent != run_dir
        ):
            raise SerialLogStoreError(
                "Borrowed monitor reconciliation lease does not cover this run."
            )
        current_manifest = load_manifest(run_dir)
        if current_manifest is None:
            raise SerialLogStoreError(
                "Monitor manifest is unavailable for reconciliation."
            )
        manifest = current_manifest
        log_scope = LogScope.bound(
            project_id=binding.project_id,
            log_root=binding.log_root,
        )
        legacy_projection = _resolve_legacy_stale_projection(
            binding,
            manifest,
            log_scope,
        )
        existing_artifact_marker = load_serial_run_artifact_marker(run_dir)
        canonical_marker = _build_terminal_marker(
            binding,
            manifest,
            artifact_marker=existing_artifact_marker,
            event_uuid=(
                str(legacy_projection["event"]["event_uuid"])
                if legacy_projection is not None
                else None
            ),
            event_origin=(
                str(legacy_projection["event_origin"])
                if legacy_projection is not None
                else None
            ),
        )
        artifact_marker = freeze_serial_run_terminal_marker(
            binding.log_root,
            run_id,
            canonical_marker,
        )
        marker = _validated_terminal_marker(
            binding,
            artifact_marker["terminal_marker"],
            canonical=canonical_marker,
        )
        event_uuid = str(marker["event_uuid"])
        last_error = marker.get("last_error")
        detected_error = marker.get("detected_error")
        raw_logs, errors, artifact_bundle_sha256 = _terminal_artifacts(
            binding,
            manifest,
            marker,
        )
        if legacy_projection is None:
            finalize_status = str(marker["run_status"])
            finalize_ended_at = str(marker["terminal_at"])
            finalize_summary: str | None = str(marker["message"])
            finalize_run_payload: dict | None = {
                "state": marker["state"],
                "last_error": last_error,
                "detected_error": detected_error,
                "monitor_terminal_event_uuid": event_uuid,
                "monitor_artifact_bundle_sha256": artifact_bundle_sha256,
            }
            finalize_event = {
                "event_uuid": event_uuid,
                "ts": str(marker["terminal_at"]),
                "phase": "complete",
                "level": str(marker["level"]),
                "tool": str(marker["tool"]),
                "source": str(marker["source"]),
                "message": str(marker["message"]),
                "payload_json": {
                    "state": marker["state"],
                    "last_error": last_error,
                    "detected_error": detected_error,
                    "detected_error_at": marker.get("detected_error_at"),
                    "terminal_marker_id": marker["marker_id"],
                    "artifact_bundle_sha256": artifact_bundle_sha256,
                },
            }
        else:
            stored_run = legacy_projection["run"]
            stored_event = legacy_projection["event"]
            finalize_status = str(stored_run["status"])
            finalize_ended_at = str(stored_run["ended_at"])
            finalize_summary = None
            finalize_run_payload = None
            finalize_event = stored_event
        report = log_repository.finalize_existing_run_with_artifacts(
            log_scope.database_file,
            project_id=binding.project_id,
            run_id=run_id,
            expected_task_type="serial_monitor",
            status=finalize_status,
            ended_at=finalize_ended_at,
            summary=finalize_summary,
            run_payload=finalize_run_payload,
            event_uuid=str(finalize_event["event_uuid"]),
            ts=str(finalize_event["ts"]),
            phase=str(finalize_event["phase"]),
            level=str(finalize_event["level"]),
            tool=str(finalize_event["tool"]),
            source=str(finalize_event["source"]),
            message=str(finalize_event["message"]),
            event_payload=(
                dict(finalize_event["payload_json"])
                if isinstance(finalize_event.get("payload_json"), dict)
                else {}
            ),
            artifacts=log_repository.EventArtifacts(
                raw_logs=raw_logs,
                errors=errors,
            ),
        )
        artifact_marker = mark_serial_run_artifacts_reconciled(
            binding.log_root,
            run_id,
            event_uuid=event_uuid,
            expected_terminal_marker=marker,
            mark_sqlite_reconciled=bool(marker.get("stale_recovery")),
        )
        mirror_report = mirror_committed_event_and_refresh_latest(
            report["event"],
            log_scope,
        )
        mirror_error = "; ".join(
            str(warning) for warning in mirror_report.get("warnings", [])
        )
        artifact_marker = mark_serial_run_audit_mirror(
            binding.log_root,
            run_id,
            event_uuid=event_uuid,
            succeeded=bool(mirror_report.get("ok")),
            error=mirror_error or None,
        )
        result = {
            "ok": True,
            "database_persisted": True,
            "audit_mirror_persisted": bool(mirror_report.get("ok")),
            "event": report["event"],
            "run": report["run"],
            "manifest": load_manifest(run_dir) or manifest,
            "artifact_marker": artifact_marker,
        }
        if mirror_error:
            result["logging_warning"] = mirror_error
        return result
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, SerialLogReconciliationBusy) or artifact_marker is None:
            persisted_artifact = artifact_marker
        else:
            try:
                persisted_artifact = record_serial_run_artifact_reconciliation_error(
                    binding.log_root,
                    run_id,
                    message,
                )
            except Exception:
                persisted_artifact = artifact_marker
        result = {
            "ok": False,
            "error_kind": getattr(
                exc,
                "error_kind",
                "monitor_artifact_reconciliation_failed",
            ),
            "message": message,
            "manifest": manifest,
            "artifact_marker": persisted_artifact,
        }
        if isinstance(exc, SerialLogReconciliationBusy):
            result["recoverable"] = True
        return result
    finally:
        if owns_lease and active_lease is not None:
            active_lease.release()


class MonitorSession:
    def __init__(self, binding: MonitorBinding, serial_module: Any):
        self.binding = binding
        self.serial_module = serial_module
        self.state = MonitorState.STARTING
        self.started_at: str | None = None
        self.stopped_at: str | None = None
        self.last_data_at: str | None = None
        self.bytes_received = 0
        self.dropped_bytes = 0
        self.unpersisted_bytes = 0
        self.last_error: dict | None = None
        self.detected_error: dict | None = None
        self._detected_error_announced = False
        self._error_detector = MicroPythonErrorDetector()
        self._next_seq = 1
        self._dropped_before_seq: int | None = None
        self._records: deque[SerialRecord] = deque()
        self._buffered_bytes = 0
        self._buffer_limit = _env_positive_int("ESP_MCP_MONITOR_BUFFER_BYTES", DEFAULT_BUFFER_BYTES)
        self._condition = threading.Condition(threading.RLock())
        self._reconcile_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._serial: Any | None = None
        self._lease: PortLease | None = None
        self._thread = threading.Thread(
            target=self._worker,
            name=f"esp-monitor-{binding.run_id}",
            daemon=True,
        )
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending_raw = b""
        self._sqlite_artifacts_reconciliation_version = 0
        self._sqlite_artifacts_reconciliation_error: str | None = None
        self._logging_persistence_state = "not_terminal"
        self.startup_recovery_reports: list[dict] = []
        self.startup_recovery_report_count = 0
        self.startup_recovery_failure_count = 0
        self._store = SerialLogStore(
            binding.log_root,
            binding.run_id,
            {
                "run_id": binding.run_id,
                "project_id": binding.project_id,
                "session_name": binding.session_name,
                "port": binding.port,
                "port_identity": binding.port_identity,
                "baudrate": binding.baudrate,
                "state": self.state.value,
                "process_owner": current_process_owner(),
            },
        )

    def start(self) -> None:
        self._thread.start()

    def wait_until_ready(self, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + max(timeout, 0)
        with self._condition:
            while self.state == MonitorState.STARTING and time.monotonic() < deadline:
                self._condition.wait(max(0, deadline - time.monotonic()))

    def _transition(self, state: MonitorState) -> None:
        with self._condition:
            if state == self.state:
                return
            if state not in _TRANSITIONS[self.state]:
                raise RuntimeError(f"Invalid monitor state transition: {self.state.value} -> {state.value}")
            self.state = state
            if state == MonitorState.RUNNING:
                self.started_at = now_utc_iso()
            if state.value in TERMINAL_STATES:
                self.stopped_at = now_utc_iso()
                self._logging_persistence_state = "pending"
            self._condition.notify_all()

    def _safe_manifest_update(self) -> None:
        try:
            self._store.update_manifest(**self.status())
        except OSError as exc:
            self.last_error = _error_payload("monitor_manifest_write_failed", exc)

    def _record_serial_cleanup_errors(self, cleanup_errors: list[str]) -> None:
        if not cleanup_errors:
            return
        with self._condition:
            if self.last_error is None:
                self.last_error = _error_payload(
                    "serial_close_failed",
                    RuntimeError("; ".join(cleanup_errors)),
                )
                self.last_error["cleanup_errors"] = list(cleanup_errors)
                self.last_error["cleanup_completed"] = False
            else:
                existing = list(self.last_error.get("cleanup_errors") or [])
                self.last_error["cleanup_errors"] = [*existing, *cleanup_errors]
                self.last_error["cleanup_completed"] = False
            self._condition.notify_all()

    def _record_log_close_failure(self, exc: BaseException) -> None:
        self.last_error = _error_payload(
            "monitor_log_close_failed",
            exc,
        )
        with self._condition:
            self.state = MonitorState.FAILED
            if self.stopped_at is None:
                self.stopped_at = now_utc_iso()
            self._logging_persistence_state = "failed"
            self._sqlite_artifacts_reconciliation_error = (
                f"{type(exc).__name__}: {exc}"
            )
            self._condition.notify_all()
        try:
            failed_status = self.status()
            failed_status["worker_alive"] = False
            self._store.update_manifest(**failed_status)
        except Exception:
            pass

    def _retry_log_store_close(self) -> bool:
        if self._store.closed:
            return True
        manifest = load_manifest(self._store.run_dir) or {}
        terminal_message = manifest.get("terminal_message")
        if not isinstance(terminal_message, str) or not terminal_message:
            terminal_message = (
                str(self.last_error.get("message"))
                if isinstance(self.last_error, dict) and self.last_error.get("message")
                else "Serial monitor ended while finalizing its log."
            )
        try:
            final_status = self.status()
            final_status["worker_alive"] = False
            final_status["terminal_message"] = terminal_message
            self._store.close(**final_status)
            return True
        except Exception as exc:
            self._record_log_close_failure(exc)
            return False

    def _log_scope(self) -> LogScope:
        return LogScope.bound(
            project_id=self.binding.project_id,
            log_root=self.binding.log_root,
        )

    def _emit(
        self,
        level: str,
        message: str,
        data: dict | None = None,
        *,
        phase: str = "execute",
    ) -> None:
        try:
            write_event(
                "esp_serial_monitor",
                level,
                message,
                data or {},
                run_id=self.binding.run_id,
                phase=phase,
                source="esp32",
                scope=self._log_scope(),
            )
        except Exception:
            pass

    def _detect_error(self, text: str) -> None:
        report = self._error_detector.feed(text)
        if report is None:
            return
        should_emit = (
            not self._detected_error_announced
            and bool(report.get("exception_type"))
        )
        persistence_error: str | None = None
        if should_emit:
            detected_at = now_utc_iso()
            try:
                freeze_serial_run_first_runtime_error(
                    self.binding.log_root,
                    self.binding.run_id,
                    report=report,
                    detected_at=detected_at,
                )
            except Exception as exc:
                persistence_error = f"{type(exc).__name__}: {exc}"
        with self._condition:
            self.detected_error = report
            if should_emit:
                self._detected_error_announced = True
            if persistence_error is not None:
                self._logging_persistence_state = "failed"
                self._sqlite_artifacts_reconciliation_error = persistence_error
            self._condition.notify_all()
        if should_emit:
            self._emit(
                "error",
                "MicroPython runtime error detected.",
                {"has_error": True, "error_report": report},
            )

    def _append_record(self, raw: bytes) -> None:
        if not raw:
            return
        timestamp = now_utc_iso()
        seq = self._next_seq
        try:
            stored = self._store.append(seq, timestamp, raw)
        except SerialLogQuotaError:
            self.unpersisted_bytes += len(raw)
            raise
        except (OSError, SerialLogStoreError) as exc:
            self.unpersisted_bytes += len(raw)
            raise SerialLogWriteFailure(exc) from exc
        record = SerialRecord(
            seq=seq,
            timestamp_utc=timestamp,
            raw=raw,
            decode_error=bool(stored.get("decode_error")),
        )
        with self._condition:
            self._next_seq += 1
            self._records.append(record)
            self._buffered_bytes += len(raw)
            while self._records and self._buffered_bytes > self._buffer_limit:
                removed = self._records.popleft()
                self._buffered_bytes -= len(removed.raw)
                self.dropped_bytes += len(removed.raw)
                self._dropped_before_seq = removed.seq
            self.last_data_at = timestamp
            self._condition.notify_all()

    def _consume(self, data: bytes) -> None:
        with self._condition:
            self.bytes_received += len(data)
        for offset in range(0, len(data), _INPUT_SLICE_BYTES):
            current = data[offset : offset + _INPUT_SLICE_BYTES]
            combined = self._pending_raw + current
            decoded = self._decoder.decode(current, final=False)
            pending, _flag = self._decoder.getstate()
            consumed_length = len(combined) - len(pending)
            consumed = combined[:consumed_length]
            self._pending_raw = bytes(pending)
            if consumed:
                self._append_record(consumed)
            if decoded:
                self._detect_error(decoded)

    def _flush_decoder(self) -> None:
        decoded = self._decoder.decode(b"", final=True)
        if decoded:
            self._detect_error(decoded)
        pending = self._pending_raw
        self._pending_raw = b""
        if pending:
            self._append_record(pending)

    def _reconcile_terminal_artifacts(self) -> dict:
        with self._reconcile_lock:
            manifest = load_manifest(self._store.run_dir)
            if manifest is None:
                result = {
                    "ok": False,
                    "message": "Monitor manifest is unavailable for reconciliation.",
                }
            else:
                result = _reconcile_terminal_manifest(self.binding, manifest)
            artifact_marker = result.get("artifact_marker")
            if not isinstance(artifact_marker, dict):
                try:
                    artifact_marker = load_serial_run_artifact_marker(
                        self._store.run_dir
                    )
                except SerialLogStoreError:
                    artifact_marker = None
            if isinstance(artifact_marker, dict):
                projection = artifact_marker.get("projection")
                sqlite_state = (
                    str(projection.get("state"))
                    if isinstance(projection, dict)
                    else "failed"
                )
                audit_mirror = artifact_marker.get("audit_mirror")
                audit_state = (
                    str(audit_mirror.get("state"))
                    if isinstance(audit_mirror, dict)
                    else "failed"
                )
                if sqlite_state == "failed" or (
                    sqlite_state == "committed" and audit_state == "failed"
                ):
                    persistence_state = "failed"
                elif sqlite_state == "committed" and audit_state == "committed":
                    persistence_state = "committed"
                elif sqlite_state == "not_terminal":
                    persistence_state = "not_terminal"
                else:
                    persistence_state = "pending"
                self._logging_persistence_state = persistence_state
                self._sqlite_artifacts_reconciliation_version = (
                    SQLITE_ARTIFACT_RECONCILIATION_VERSION
                    if sqlite_state == "committed"
                    else 0
                )
                sqlite_error = (
                    projection.get("error")
                    if isinstance(projection, dict)
                    else result.get("message")
                )
                audit_error = (
                    audit_mirror.get("error")
                    if isinstance(audit_mirror, dict)
                    else None
                )
                error = sqlite_error or audit_error
                self._sqlite_artifacts_reconciliation_error = (
                    str(error) if error else None
                )
            elif result.get("message"):
                self._logging_persistence_state = "failed"
                self._sqlite_artifacts_reconciliation_error = str(
                    result["message"]
                )
            return result

    def _worker(self) -> None:
        terminal_message = "Serial monitor stopped."
        try:
            self._lease = PortLease.acquire(
                self.binding.port_identity,
                run_id=self.binding.run_id,
                project_id=self.binding.project_id,
            )
            if self._lease.stale_owner:
                self._emit("warning", "Recovered a stale serial monitor lock.", {"owner": self._lease.stale_owner})
            if self._stop_event.is_set():
                self._transition(MonitorState.STOPPED)
                return
            self._serial = _open_serial(self.serial_module, self.binding)
            if self._stop_event.is_set():
                if self.state == MonitorState.STARTING:
                    self._transition(MonitorState.STOPPING)
            else:
                self._transition(MonitorState.RUNNING)
                self._safe_manifest_update()
                self._emit(
                    "info",
                    f"Serial monitor started on {self.binding.port}.",
                    {"port": self.binding.port, "baudrate": self.binding.baudrate},
                )

            while not self._stop_event.is_set():
                data = _read_serial_chunk(self._serial)
                if data:
                    self._consume(data)

            if self.state == MonitorState.RUNNING:
                self._transition(MonitorState.STOPPING)
            self._flush_decoder()
            if self.state == MonitorState.STOPPING:
                self._transition(MonitorState.STOPPED)
        except PortLockError as exc:
            self.last_error = {
                **_error_payload("serial_port_locked", exc),
                "owner": exc.owner,
            }
            if self.state in {MonitorState.STARTING, MonitorState.STOPPING}:
                self._transition(MonitorState.FAILED)
            terminal_message = "Serial monitor failed because the port is reserved."
        except SerialLogQuotaError as exc:
            self.last_error = _error_payload("serial_log_quota_exceeded", exc)
            if self.state in {MonitorState.STARTING, MonitorState.RUNNING, MonitorState.STOPPING}:
                self._transition(MonitorState.FAILED)
            terminal_message = "Serial monitor stopped because its log quota was exceeded."
        except SerialLogWriteFailure as exc:
            error_kind = "serial_log_disk_full" if getattr(exc.cause, "errno", None) == 28 else "serial_log_write_failed"
            self.last_error = _error_payload(error_kind, exc.cause)
            if self.state in {MonitorState.STARTING, MonitorState.RUNNING, MonitorState.STOPPING}:
                self._transition(MonitorState.FAILED)
            terminal_message = "Serial monitor stopped because its log could not be written."
        except Exception as exc:
            if self.state == MonitorState.STOPPING and self._stop_event.is_set():
                terminal_message = "Serial monitor stopped."
            else:
                disconnected = self.state == MonitorState.RUNNING and _looks_disconnected(exc)
                opening = self.state == MonitorState.STARTING
                error_kind = "serial_port_open_failed" if opening else (
                    "serial_disconnected" if disconnected else "serial_monitor_failed"
                )
                self.last_error = _error_payload(error_kind, exc)
                target = MonitorState.DISCONNECTED if disconnected else MonitorState.FAILED
                if target in _TRANSITIONS[self.state]:
                    self._transition(target)
                terminal_message = "Serial monitor disconnected." if disconnected else (
                    "Serial monitor could not open the requested port." if opening else "Serial monitor failed."
                )
        finally:
            serial_port = self._serial
            if serial_port is not None:
                cleanup_errors = deactivate_and_close_serial(serial_port)
                self._record_serial_cleanup_errors(cleanup_errors)
            if self._lease is not None:
                self._lease.release()
            if self.state == MonitorState.STARTING:
                self._transition(MonitorState.FAILED)
            elif self.state == MonitorState.STOPPING:
                self._transition(MonitorState.STOPPED)
            store_closed = False
            try:
                final_status = self.status()
                final_status["worker_alive"] = False
                final_status["terminal_message"] = terminal_message
                self._store.close(**final_status)
                store_closed = True
            except Exception as exc:
                self._record_log_close_failure(exc)
            if store_closed:
                result = self._reconcile_terminal_artifacts()
                if not result.get("ok"):
                    self._sqlite_artifacts_reconciliation_error = str(
                        result.get("message")
                        or "Monitor artifacts were not persisted to SQLite."
                    )
            with self._condition:
                self._condition.notify_all()

    def request_stop(self, timeout: float) -> dict:
        with self._condition:
            if self.state.value in TERMINAL_STATES:
                serial_port = self._serial
            else:
                if self.state in {MonitorState.STARTING, MonitorState.RUNNING}:
                    self._transition(MonitorState.STOPPING)
                self._stop_event.set()
                serial_port = self._serial
        if serial_port is not None:
            cancel_read = getattr(serial_port, "cancel_read", None)
            if callable(cancel_read):
                try:
                    cancel_read()
                except Exception:
                    pass
        self._thread.join(max(timeout, 0))
        if self._thread.is_alive() and serial_port is not None:
            cleanup_errors = deactivate_and_close_serial(serial_port)
            self._record_serial_cleanup_errors(cleanup_errors)
            self._thread.join(0.25)
        if not self._thread.is_alive() and not self._store.closed:
            self._retry_log_store_close()
        if not self._thread.is_alive() and self._store.closed:
            self._reconcile_terminal_artifacts()
        result = self.status()
        result["cleanup_complete"] = (
            not self._thread.is_alive() and self._store.closed
        )
        return result

    def status(self) -> dict:
        with self._condition:
            return {
                "run_id": self.binding.run_id,
                "project_id": self.binding.project_id,
                "session_name": self.binding.session_name,
                "port": self.binding.port,
                "port_identity": self.binding.port_identity,
                "baudrate": self.binding.baudrate,
                "state": self.state.value,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "last_data_at": self.last_data_at,
                "bytes_received": self.bytes_received,
                "persisted_bytes": self._store.persisted_bytes,
                "buffered_bytes": self._buffered_bytes,
                "dropped_bytes": self.dropped_bytes,
                "unpersisted_bytes": self.unpersisted_bytes,
                "last_error": self.last_error,
                "detected_error": self.detected_error,
                "worker_alive": self._thread.is_alive(),
                "log_store_closed": self._store.closed,
                "log_dir": str(self._store.run_dir),
                "next_seq": self._next_seq,
                "sqlite_artifacts_reconciliation_version": (
                    self._sqlite_artifacts_reconciliation_version
                ),
                "sqlite_artifacts_reconciliation_error": (
                    self._sqlite_artifacts_reconciliation_error
                ),
                "logging_persistence_state": self._logging_persistence_state,
                "logging_persisted": (
                    True
                    if self._logging_persistence_state == "committed"
                    else (
                        False
                        if self._logging_persistence_state == "failed"
                        else None
                    )
                ),
            }

    def read(self, *, after_seq: int | None, max_bytes: int, wait_ms: int, representation: str) -> dict:
        deadline = time.monotonic() + wait_ms / 1000
        with self._condition:
            while True:
                available = any(after_seq is None or record.seq > after_seq for record in self._records)
                if available or self.state.value in TERMINAL_STATES or wait_ms == 0:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)

            selected: list[dict] = []
            used = 0
            for record in self._records:
                if after_seq is not None and record.seq <= after_seq:
                    continue
                if selected and used + len(record.raw) > max_bytes:
                    break
                payload: dict[str, Any] = {
                    "seq": record.seq,
                    "timestamp_utc": record.timestamp_utc,
                    "raw_size": len(record.raw),
                    "decode_error": record.decode_error,
                }
                if representation in {"text", "both"}:
                    payload["text"] = record.raw.decode("utf-8", errors="replace")
                if representation in {"base64", "both"}:
                    payload["raw_base64"] = base64.b64encode(record.raw).decode("ascii")
                selected.append(payload)
                used += len(record.raw)
                if used >= max_bytes:
                    break

            last_seq = selected[-1]["seq"] if selected else after_seq
            return {
                "run_id": self.binding.run_id,
                "records": selected,
                "next_after_seq": last_seq,
                "next_seq": self._next_seq,
                "dropped_before_seq": self._dropped_before_seq,
                "state": self.state.value,
                "detected_error": self.detected_error,
            }


class SerialMonitorManager:
    def __init__(self):
        self._sessions: dict[str, MonitorSession] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _reconcile_recovered_manifest(
        binding: MonitorBinding,
        manifest: dict,
        *,
        borrowed_lease: SerialRunReconciliationLease | None = None,
    ) -> dict | None:
        manifest_project_id = str(manifest.get("project_id") or "")
        run_id = str(manifest.get("run_id") or "")
        if manifest_project_id != binding.project_id or not run_id:
            return None
        recovery_error = manifest.get("_sqlite_artifact_recovery_error")
        if isinstance(recovery_error, str) and recovery_error:
            return {
                "ok": False,
                "error_kind": "monitor_artifact_recovery_failed",
                "message": recovery_error,
                "manifest": manifest,
                "artifact_marker": None,
            }
        recovered_binding = MonitorBinding(
            run_id=run_id,
            project_id=binding.project_id,
            project_dir=binding.project_dir,
            log_root=binding.log_root,
            session_name=str(manifest.get("session_name") or "recovered"),
            port=str(manifest.get("port") or binding.port),
            port_identity=(
                dict(manifest["port_identity"])
                if isinstance(manifest.get("port_identity"), dict)
                else binding.port_identity
            ),
            baudrate=(
                int(manifest["baudrate"])
                if isinstance(manifest.get("baudrate"), int)
                else binding.baudrate
            ),
        )
        return _reconcile_terminal_manifest(
            recovered_binding,
            manifest,
            borrowed_lease=borrowed_lease,
        )

    @classmethod
    def _reconcile_recovered_runs(
        cls,
        binding: MonitorBinding,
        recovered: list[dict],
    ) -> list[dict]:
        reports: list[dict] = []
        for manifest in recovered:
            report = cls._reconcile_recovered_manifest(binding, manifest)
            if report is not None:
                reports.append(report)
        return reports

    def start(self, binding: MonitorBinding, serial_module: Any) -> MonitorSession:
        port_key = identity_key(binding.port_identity)
        with self._lock:
            active_run_ids = {
                session.binding.run_id
                for session in self._sessions.values()
                if session.status()["state"] not in TERMINAL_STATES
            }
        recovery_reports = recover_serial_runs(
            binding.log_root,
            skip_run_ids=active_run_ids,
            project_id=binding.project_id,
            reconciliation_consumer=lambda manifest, borrowed_lease: (
                self._reconcile_recovered_manifest(
                    binding,
                    manifest,
                    borrowed_lease=borrowed_lease,
                )
                or {
                    "ok": False,
                    "error_kind": "monitor_artifact_recovery_identity_conflict",
                    "message": (
                        "Recovered monitor identity does not match the active "
                        "project binding."
                    ),
                    "manifest": manifest,
                    "artifact_marker": None,
                }
            ),
        )
        failed_recovery_reports = [
            report for report in recovery_reports if not report.get("ok")
        ]
        ordered_recovery_reports = [
            *failed_recovery_reports,
            *[report for report in recovery_reports if report.get("ok")],
        ]
        bounded_recovery_reports: list[dict] = []
        for report in ordered_recovery_reports[:20]:
            manifest = (
                report.get("manifest")
                if isinstance(report.get("manifest"), dict)
                else {}
            )
            event = (
                report.get("event")
                if isinstance(report.get("event"), dict)
                else {}
            )
            bounded_recovery_reports.append(
                {
                    "ok": bool(report.get("ok")),
                    "run_id": str(
                        manifest.get("run_id")
                        or event.get("run_id")
                        or ""
                    ),
                    "error_kind": (
                        str(report["error_kind"])
                        if report.get("error_kind")
                        else None
                    ),
                    "message": (
                        str(report["message"])[:512]
                        if report.get("message")
                        else None
                    ),
                    "database_persisted": report.get(
                        "database_persisted"
                    ),
                    "audit_mirror_persisted": report.get(
                        "audit_mirror_persisted"
                    ),
                    "recoverable": report.get("recoverable"),
                }
            )
        with self._lock:
            for session in self._sessions.values():
                status = session.status()
                if status["state"] in TERMINAL_STATES:
                    continue
                if session.binding.project_id == binding.project_id and session.binding.session_name == binding.session_name:
                    raise MonitorConflictError(
                        "monitor_session_conflict",
                        f"Session name {binding.session_name!r} is already active in this project.",
                    )
                if identity_key(session.binding.port_identity) == port_key:
                    raise MonitorConflictError(
                        "serial_port_monitored",
                        f"Serial port {binding.port!r} is already monitored by run {session.binding.run_id}.",
                    )
            session = MonitorSession(binding, serial_module)
            session.startup_recovery_reports = bounded_recovery_reports
            session.startup_recovery_report_count = len(recovery_reports)
            session.startup_recovery_failure_count = len(
                failed_recovery_reports
            )
            self._sessions[binding.run_id] = session
            session.start()
        session.wait_until_ready()
        return session

    def _session_for_project(self, run_id: str, project_id: str) -> MonitorSession | None:
        with self._lock:
            session = self._sessions.get(run_id)
        if session is None or session.binding.project_id != project_id:
            return None
        return session

    def stop(self, run_id: str, project_id: str, timeout: float) -> dict | None:
        session = self._session_for_project(run_id, project_id)
        return None if session is None else session.request_stop(timeout)

    def status(self, project_id: str, run_id: str | None = None) -> list[dict]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [
            session.status()
            for session in sessions
            if session.binding.project_id == project_id and (run_id is None or session.binding.run_id == run_id)
        ]

    def persisted_status(self, log_root: Path, run_id: str) -> dict | None:
        run_dir = log_root / "serial" / run_id
        manifest = load_manifest(run_dir)
        if manifest is None:
            return None
        status = dict(manifest)
        artifact_marker: dict | None = None
        sqlite_projection_verified = False
        try:
            artifact_marker = load_serial_run_artifact_marker(run_dir)
            projection = (
                artifact_marker.get("projection")
                if isinstance(artifact_marker, dict)
                else None
            )
            if isinstance(projection, dict):
                sqlite_state = str(projection.get("state") or "failed")
                audit_mirror = artifact_marker.get("audit_mirror")
                audit_state = (
                    str(audit_mirror.get("state") or "failed")
                    if isinstance(audit_mirror, dict)
                    else "failed"
                )
                persistence_error = projection.get("error") or (
                    audit_mirror.get("error")
                    if isinstance(audit_mirror, dict)
                    else None
                )
                if sqlite_state == "committed":
                    project_id = str(manifest.get("project_id") or "")
                    binding = MonitorBinding(
                        run_id=run_id,
                        project_id=project_id,
                        project_dir=log_root.parent,
                        log_root=log_root,
                        session_name=str(
                            manifest.get("session_name") or "persisted"
                        ),
                        port=str(manifest.get("port") or ""),
                        port_identity=(
                            dict(manifest["port_identity"])
                            if isinstance(
                                manifest.get("port_identity"),
                                dict,
                            )
                            else {}
                        ),
                        baudrate=(
                            int(manifest["baudrate"])
                            if isinstance(manifest.get("baudrate"), int)
                            else 115200
                        ),
                    )
                    try:
                        event, log_scope = (
                            _validated_committed_terminal_projection(
                                binding,
                                manifest,
                                artifact_marker,
                            )
                        )
                        sqlite_projection_verified = True
                        if audit_state == "committed" and not (
                            committed_event_and_latest_mirrors_match(
                                event,
                                log_scope,
                            )
                        ):
                            audit_state = "failed"
                            persistence_error = (
                                "Committed monitor audit mirrors are missing "
                                "or conflict."
                            )
                    except Exception as exc:
                        sqlite_state = "failed"
                        persistence_error = (
                            f"{type(exc).__name__}: {exc}"
                        )
                if sqlite_state == "failed" or (
                    sqlite_state == "committed" and audit_state == "failed"
                ):
                    persistence_state = "failed"
                elif sqlite_state == "committed" and audit_state == "committed":
                    persistence_state = "committed"
                elif sqlite_state == "not_terminal":
                    persistence_state = "not_terminal"
                else:
                    persistence_state = "pending"
            elif str(manifest.get("state") or "") in TERMINAL_STATES:
                persistence_state = "pending"
                persistence_error = None
            else:
                persistence_state = "not_terminal"
                persistence_error = None
        except SerialLogStoreError as exc:
            persistence_state = "failed"
            persistence_error = f"{type(exc).__name__}: {exc}"
        status["logging_persistence_state"] = persistence_state
        status["sqlite_artifacts_reconciliation_version"] = (
            SQLITE_ARTIFACT_RECONCILIATION_VERSION
            if sqlite_projection_verified
            else 0
        )
        status["sqlite_artifacts_reconciliation_error"] = (
            str(persistence_error) if persistence_error else None
        )
        status["logging_persisted"] = (
            True
            if persistence_state == "committed"
            else (False if persistence_state == "failed" else None)
        )
        return status

    def reconcile_persisted(
        self,
        *,
        log_root: Path,
        run_id: str,
        project_id: str,
    ) -> dict | None:
        manifest = load_manifest(log_root / "serial" / run_id)
        if manifest is None or manifest.get("project_id") != project_id:
            return None
        if str(manifest.get("state") or "") not in TERMINAL_STATES:
            reports = recover_serial_runs(
                log_root,
                include_run_ids={run_id},
                project_id=project_id,
                reconciliation_consumer=(
                    lambda recovered_manifest, borrowed_lease: (
                        self._reconcile_recovered_manifest(
                            MonitorBinding(
                                run_id=run_id,
                                project_id=project_id,
                                project_dir=log_root.parent,
                                log_root=log_root,
                                session_name=str(
                                    recovered_manifest.get("session_name")
                                    or "persisted"
                                ),
                                port=str(
                                    recovered_manifest.get("port") or ""
                                ),
                                port_identity=(
                                    dict(
                                        recovered_manifest["port_identity"]
                                    )
                                    if isinstance(
                                        recovered_manifest.get(
                                            "port_identity"
                                        ),
                                        dict,
                                    )
                                    else {}
                                ),
                                baudrate=(
                                    int(recovered_manifest["baudrate"])
                                    if isinstance(
                                        recovered_manifest.get("baudrate"),
                                        int,
                                    )
                                    else 115200
                                ),
                            ),
                            recovered_manifest,
                            borrowed_lease=borrowed_lease,
                        )
                        or {
                            "ok": False,
                            "error_kind": (
                                "monitor_artifact_recovery_identity_conflict"
                            ),
                            "message": (
                                "Recovered monitor identity does not match "
                                "the persisted run."
                            ),
                            "manifest": recovered_manifest,
                            "artifact_marker": None,
                        }
                    )
                ),
            )
            if reports:
                return reports[0]
            return {
                "ok": False,
                "error_kind": "monitor_run_not_terminal",
                "message": (
                    "Persisted monitor run is still owned by a live process "
                    "or could not be recovered."
                ),
                "manifest": manifest,
                "artifact_marker": None,
                "recoverable": True,
            }
        binding = MonitorBinding(
            run_id=run_id,
            project_id=project_id,
            project_dir=log_root.parent,
            log_root=log_root,
            session_name=str(manifest.get("session_name") or "persisted"),
            port=str(manifest.get("port") or ""),
            port_identity=(
                dict(manifest["port_identity"])
                if isinstance(manifest.get("port_identity"), dict)
                else {}
            ),
            baudrate=(
                int(manifest["baudrate"])
                if isinstance(manifest.get("baudrate"), int)
                else 115200
            ),
        )
        return _reconcile_terminal_manifest(binding, manifest)

    def read(
        self,
        *,
        project_id: str,
        log_root: Path,
        run_id: str,
        after_seq: int | None,
        max_bytes: int,
        wait_ms: int,
        representation: str,
    ) -> dict:
        session = self._session_for_project(run_id, project_id)
        if session is not None:
            return session.read(
                after_seq=after_seq,
                max_bytes=max_bytes,
                wait_ms=wait_ms,
                representation=representation,
            )
        return read_persisted_records(
            log_root / "serial" / run_id,
            after_seq=after_seq,
            max_bytes=max_bytes,
            representation=representation,
        )

    def shutdown_all(self, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + max(timeout, 0)
        with self._lock:
            sessions = list(self._sessions.values())
        results = []
        for session in sessions:
            remaining = max(0, deadline - time.monotonic())
            results.append(session.request_stop(remaining))
        return {
            "ok": all(not result.get("worker_alive") for result in results),
            "sessions": results,
        }


SERIAL_MONITOR_MANAGER = SerialMonitorManager()
