from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import serial_monitor_store
from ..database import log_repository
from ..database.event_repository import (
    normalize_event_uuid,
    normalize_level,
    normalize_phase,
    normalize_timestamp,
)
from ..tools.log_tools import LogScope


HISTORICAL_CAPTURE_RECONCILIATION_VERSION = 1
HISTORICAL_CAPTURE_ADAPTER_ID = "historical_serial_capture_jsonl_v1"
MAX_SOURCE_BYTES = 1024 * 1024
MAX_SOURCE_RECORDS = 10_000
MAX_SOURCE_LINE_BYTES = 256 * 1024
MAX_CAPTURE_BYTES = 256 * 1024 * 1024

_SAFE_SOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}\.jsonl$")
_SAFE_RAW_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}\.log$")
_MODERN_RAW_NAME = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}_"
    r"\d{8}_\d{6}_[0-9a-f]{12}\.log$"
)


class HistoricalCaptureResolutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_kind: str = "historical_capture_resolution_failed",
    ):
        super().__init__(message)
        self.error_kind = error_kind


@dataclass(frozen=True)
class HistoricalSerialCaptureArtifactCandidate:
    status: str
    adapter_id: str
    reconciliation_version: int
    source_format: str
    project_id: str
    run_id: str
    requested_event_uuid: str
    source_path: str
    source_sha256: str
    source_size: int
    source_record_count: int
    source_record_number: int
    source_record_sha256: str
    expected_event_phase: str
    expected_run_status: str
    database_projection_eligible: bool
    database_projection_reason: str | None
    artifacts: log_repository.EventArtifacts
    raw_size: int | None
    raw_bytes_exact: bool | None
    artifact_content_kind: str | None
    expected_event_profile_sha256: str
    artifact_bundle_sha256: str
    _expected_event_profile_json: str
    _expected_run_profile_json: str

    @property
    def expected_event_profile(self) -> dict[str, Any]:
        value = json.loads(self._expected_event_profile_json)
        if not isinstance(value, dict):
            raise RuntimeError("Historical capture event profile snapshot is invalid.")
        return value

    @property
    def expected_run_profile(self) -> dict[str, Any]:
        value = json.loads(self._expected_run_profile_json)
        if not isinstance(value, dict):
            raise RuntimeError("Historical capture run profile snapshot is invalid.")
        return value


@dataclass(frozen=True)
class _SourceRecord:
    number: int
    value: dict[str, Any]
    sha256: str


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_name(value: object, *, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} is not a safe direct-child name.")
    if any(character in value for character in ("\0", "\r", "\n", "/", "\\")):
        raise ValueError(f"{label} contains an unsafe character.")
    return value


def _safe_run_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(character in value for character in ("\0", "\r", "\n", "/", "\\"))
    ):
        raise ValueError("Historical capture run_id is not a safe identity.")
    return value


def _directory_snapshot(
    scope: LogScope,
) -> tuple[Path, Path, tuple[tuple[int, ...], ...]]:
    project_dir = Path(scope.project_dir)
    log_root = Path(scope.log_root)
    if (
        not project_dir.is_absolute()
        or not log_root.is_absolute()
        or log_root.parent != project_dir
        or not isinstance(scope.project_id, str)
        or not scope.project_id
    ):
        raise ValueError(
            "Historical capture log root is not bound to its project directory."
        )
    sessions_root = log_root / "sessions"
    raw_root = log_root / "raw"
    paths = (
        (project_dir, "Capture project directory"),
        (log_root, "Capture log root"),
        (sessions_root, "Capture sessions root"),
        (raw_root, "Capture raw root"),
    )
    identities = tuple(
        serial_monitor_store.safe_directory_identity(
            path,
            label=label,
            include_metadata=False,
        )
        for path, label in paths
    )
    return sessions_root, raw_root, identities


def _read_binary_snapshot(
    path: Path,
    *,
    parent: Path,
    label: str,
    max_bytes: int,
) -> tuple[bytes, int, str]:
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    with serial_monitor_store._safe_binary_reader(
        path,
        parent=parent,
        label=label,
    ) as (handle, status):
        if int(status.st_size) > max_bytes:
            raise ValueError(f"{label} exceeds the supported size.")
        while block := handle.read(min(1024 * 1024, max_bytes + 1 - total)):
            total += len(block)
            if total > max_bytes:
                raise ValueError(f"{label} exceeds the supported size.")
            digest.update(block)
            chunks.append(block)
        if total != int(status.st_size):
            raise ValueError(f"{label} size changed while it was being read.")
    return b"".join(chunks), total, digest.hexdigest()


def _read_digest_snapshot(
    path: Path,
    *,
    parent: Path,
    label: str,
    max_bytes: int,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with serial_monitor_store._safe_binary_reader(
        path,
        parent=parent,
        label=label,
    ) as (handle, status):
        if int(status.st_size) > max_bytes:
            raise ValueError(f"{label} exceeds the supported size.")
        while block := handle.read(min(1024 * 1024, max_bytes + 1 - total)):
            total += len(block)
            if total > max_bytes:
                raise ValueError(f"{label} exceeds the supported size.")
            digest.update(block)
        if total != int(status.st_size):
            raise ValueError(f"{label} size changed while it was being read.")
    return total, digest.hexdigest()


def _parse_jsonl(raw: bytes) -> list[_SourceRecord]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Historical capture JSONL is not UTF-8: {exc}") from exc
    records: list[_SourceRecord] = []
    for physical_line in text.splitlines():
        if not physical_line.strip():
            continue
        encoded_line = physical_line.encode("utf-8")
        if len(encoded_line) > MAX_SOURCE_LINE_BYTES:
            raise ValueError("Historical capture JSONL line exceeds the supported size.")
        try:
            value = json.loads(physical_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Historical capture JSONL is invalid: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("Historical capture JSONL records must be objects.")
        records.append(
            _SourceRecord(
                number=len(records) + 1,
                value=value,
                sha256=hashlib.sha256(encoded_line).hexdigest(),
            )
        )
        if len(records) > MAX_SOURCE_RECORDS:
            raise ValueError(
                "Historical capture JSONL has too many records."
            )
    if not records:
        raise ValueError("Historical capture JSONL has no records.")
    return records


def _validate_record_scope(
    records: list[_SourceRecord],
    *,
    project_id: str,
    run_id: str,
) -> None:
    for source_record in records:
        record = source_record.value
        if record.get("run_id") != run_id:
            raise ValueError(
                "Historical capture JSONL record conflicts with the requested run."
            )
        record_project = record.get("project_id")
        if record_project is not None and record_project != project_id:
            raise ValueError(
                "Historical capture JSONL record conflicts with the project."
            )


def _select_capture_record(
    records: list[_SourceRecord],
) -> tuple[_SourceRecord, str]:
    candidates: list[tuple[_SourceRecord, str]] = []
    for source_record in records:
        record = source_record.value
        if record.get("tool") != "esp_serial_capture":
            continue
        has_native_identity = "event_uuid" in record or "phase" in record
        if has_native_identity:
            if record.get("phase") == "complete":
                candidates.append((source_record, "native_complete_v1"))
        else:
            candidates.append((source_record, "legacy_single_v1"))
    if len(candidates) != 1:
        raise ValueError(
            "Historical capture JSONL must contain exactly one terminal capture record."
        )
    selected, source_format = candidates[0]
    if source_format == "legacy_single_v1":
        if len(records) != 1:
            raise ValueError(
                "Legacy historical capture JSONL must contain one record."
            )
        if "event_uuid" in selected.value or "phase" in selected.value:
            raise ValueError("Legacy historical capture identity is mixed.")
    else:
        if len(records) != 2 or selected.number != 2:
            raise ValueError(
                "Native historical capture JSONL must contain exactly "
                "prepare and complete records."
            )
        event_uuids: list[str] = []
        for source_record in records:
            record = source_record.value
            if (
                not isinstance(record.get("event_uuid"), str)
                or not isinstance(record.get("phase"), str)
                or record.get("project_id") is None
            ):
                raise ValueError(
                    "Native historical capture JSONL contains a mixed record format."
                )
            event_uuids.append(normalize_event_uuid(record["event_uuid"]))
            normalize_phase(record["phase"])
            normalize_level(str(record.get("level") or "info"))
            normalize_timestamp(str(record.get("ts") or ""))
            sequence_no = record.get("sequence_no")
            if (
                isinstance(sequence_no, bool)
                or not isinstance(sequence_no, int)
                or sequence_no != source_record.number
            ):
                raise ValueError(
                    "Native historical capture sequence is not contiguous."
                )
            if record.get("tool") != "esp_serial_capture":
                raise ValueError(
                    "Native historical capture contains another tool."
                )
            if not isinstance(record.get("payload_json"), dict):
                raise ValueError(
                    "Native historical capture payload_json must be an object."
                )
            if (
                record.get("data") is not None
                and record.get("data") != record.get("payload_json")
            ):
                raise ValueError(
                    "Native historical capture data mirror conflicts with payload_json."
                )
            event_id = record.get("event_id")
            if event_id is not None and event_id != record.get("event_uuid"):
                raise ValueError(
                    "Native historical capture event_id conflicts with event_uuid."
                )
        if [record.value["phase"] for record in records] != [
            "prepare",
            "complete",
        ]:
            raise ValueError(
                "Native historical capture phases must be prepare then complete."
            )
        if len(set(event_uuids)) != len(event_uuids):
            raise ValueError(
                "Native historical capture event UUIDs must be unique."
            )
        if any(
            record.value.get("task_type") != "serial_capture"
            for record in records
        ):
            raise ValueError(
                "Native historical capture task_type must be serial_capture."
            )
        if any(record.value.get("source") != "toolchain" for record in records):
            raise ValueError(
                "Native historical capture source must be toolchain."
            )
        if any("selected_port" not in record.value for record in records):
            raise ValueError(
                "Native historical capture selected_port metadata is missing."
            )
        selected_ports = [
            record.value["selected_port"] for record in records
        ]
        if (
            any(
                port is not None
                and (not isinstance(port, str) or not port.strip())
                for port in selected_ports
            )
            or len(set(selected_ports)) != 1
        ):
            raise ValueError(
                "Native historical capture selected_port must be present "
                "and consistent."
            )
        completion_payload = selected.value["payload_json"]
        if "port" in completion_payload:
            completion_port = completion_payload["port"]
            if (
                not isinstance(completion_port, str)
                or not completion_port.strip()
                or completion_port != selected_ports[0]
            ):
                raise ValueError(
                    "Native historical capture selected_port conflicts with "
                    "the completion payload."
                )
        elif normalize_level(str(selected.value.get("level") or "info")) not in {
            "error",
            "critical",
        }:
            raise ValueError(
                "Successful native historical capture has no completion port."
            )
    return selected, source_format


def _payload_for(
    record: dict[str, Any],
    *,
    source_format: str,
) -> dict[str, Any]:
    payload_json = record.get("payload_json")
    data = record.get("data")
    if source_format == "native_complete_v1":
        if not isinstance(payload_json, dict):
            raise ValueError(
                "Native historical capture payload_json must be an object."
            )
        if data is not None and data != payload_json:
            raise ValueError(
                "Native historical capture data mirror conflicts with payload_json."
            )
        return json.loads(json.dumps(payload_json))
    if isinstance(payload_json, dict):
        payload = payload_json
    elif isinstance(data, dict):
        payload = data
    else:
        raise ValueError("Legacy historical capture data must be an object.")
    return json.loads(json.dumps(payload))


def _legacy_raw_name(supplied_path: object) -> str:
    if not isinstance(supplied_path, str) or not supplied_path:
        raise ValueError("Historical capture has no raw_path.")
    if any(character in supplied_path for character in ("\0", "\r", "\n")):
        raise ValueError("Historical capture raw_path contains control characters.")
    lexical = supplied_path.replace("\\", "/")
    local_windows_path = bool(re.match(r"^[A-Za-z]:/", lexical))
    local_posix_path = (
        supplied_path.startswith("/")
        and not supplied_path.startswith("//")
        and "\\" not in supplied_path
    )
    if not local_windows_path and not local_posix_path:
        raise ValueError(
            "Historical capture raw_path is not a local absolute path."
        )
    parts = lexical.split("/")
    inspected_parts = parts[1:] if local_posix_path else parts
    if any(part in {"", ".", ".."} for part in inspected_parts):
        raise ValueError("Historical capture raw_path has unsafe segments.")
    if len(parts) < 3 or parts[-3:-1] != ["logs", "raw"]:
        raise ValueError(
            "Historical capture raw_path does not identify logs/raw."
        )
    return _safe_name(parts[-1], pattern=_SAFE_RAW_NAME, label="raw filename")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_positive_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _optional_recoverable(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return None


def _historical_errors(
    payload: dict[str, Any],
    *,
    event_uuid: str,
    level: str,
    event_message: str,
) -> tuple[log_repository.ErrorArtifact, ...]:
    errors: list[log_repository.ErrorArtifact] = []
    if level in {"error", "critical"}:
        error_kind = _optional_text(payload.get("error_kind"))
        if error_kind is not None:
            errors.append(
                log_repository.ErrorArtifact(
                    occurrence_key=f"event:{event_uuid}:result_error",
                    error_kind=error_kind,
                    file=_optional_text(payload.get("file")),
                    line=_optional_positive_integer(payload.get("line")),
                    column=_optional_positive_integer(payload.get("column")),
                    exception_type=_optional_text(payload.get("exception_type")),
                    message=event_message,
                    raw_text=None,
                    recoverable=_optional_recoverable(payload.get("recoverable")),
                )
            )
    report = payload.get("error_report")
    if (
        payload.get("has_error") is True
        and isinstance(report, dict)
        and report.get("has_error") is True
    ):
        error_kind = _optional_text(report.get("error_kind"))
        if error_kind is not None:
            errors.append(
                log_repository.ErrorArtifact(
                    occurrence_key=f"event:{event_uuid}:structured_error",
                    error_kind=error_kind,
                    file=_optional_text(report.get("file")),
                    line=_optional_positive_integer(report.get("line")),
                    column=_optional_positive_integer(report.get("column")),
                    exception_type=_optional_text(report.get("exception_type")),
                    message=(
                        None
                        if report.get("message") is None
                        else str(report.get("message"))
                    ),
                    raw_text=None,
                    recoverable=_optional_recoverable(
                        report.get("recoverable")
                    ),
                )
            )
    return tuple(errors)


def _artifact_bundle_sha256(
    artifacts: log_repository.EventArtifacts,
) -> str:
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
    return _sha256_json(bundle)


def resolve_historical_serial_capture_artifacts(
    scope: LogScope,
    *,
    source_name: str,
    run_id: str,
    event_uuid: str | None,
) -> HistoricalSerialCaptureArtifactCandidate:
    """Resolve one fixed-capture JSONL snapshot without mutating project state."""

    if not isinstance(event_uuid, str) or not event_uuid.strip():
        raise HistoricalCaptureResolutionError(
            "An explicit historical capture event_uuid is required.",
            error_kind="historical_capture_event_uuid_required",
        )
    try:
        requested_event_uuid = normalize_event_uuid(event_uuid)
        safe_source_name = _safe_name(
            source_name,
            pattern=_SAFE_SOURCE_NAME,
            label="source_name",
        )
        safe_run_id = _safe_run_id(run_id)
        sessions_root, raw_root, directory_snapshot = _directory_snapshot(scope)
        source_path = sessions_root / safe_source_name
        source_raw, source_size, source_sha256 = _read_binary_snapshot(
            source_path,
            parent=sessions_root,
            label="Historical capture JSONL",
            max_bytes=MAX_SOURCE_BYTES,
        )
        records = _parse_jsonl(source_raw)
        _validate_record_scope(
            records,
            project_id=scope.project_id,
            run_id=safe_run_id,
        )
        selected, source_format = _select_capture_record(records)
        record = selected.value
        payload = _payload_for(record, source_format=source_format)

        if source_format == "legacy_single_v1":
            derived_event_uuid = log_repository.legacy_jsonl_event_uuid(
                scope.project_id,
                safe_run_id,
                selected.number,
                record,
            )
            expected_phase = "unknown"
            expected_task_type = str(
                record.get("task_type") or record.get("tool")
            )
        else:
            derived_event_uuid = normalize_event_uuid(
                str(record.get("event_uuid") or "")
            )
            expected_phase = "complete"
            expected_task_type = str(
                record.get("task_type") or record.get("tool")
            )
        if derived_event_uuid != requested_event_uuid:
            raise ValueError(
                "Historical capture event identity conflicts with the request."
            )

        timestamp = normalize_timestamp(str(record.get("ts") or ""))
        level = normalize_level(str(record.get("level") or "info"))
        tool = str(record.get("tool") or "")
        if tool != "esp_serial_capture":
            raise ValueError("Historical capture tool identity is invalid.")
        source = str(record.get("source") or "legacy_jsonl")
        message = str(record.get("message") or "")
        raw_path_value = payload.get("raw_path")
        raw_logs: tuple[log_repository.RawLogArtifact, ...] = ()
        raw_size: int | None = None
        raw_bytes_exact: bool | None = None
        artifact_content_kind: str | None = None
        if raw_path_value is not None:
            raw_name = _legacy_raw_name(raw_path_value)
            raw_size, raw_sha256 = _read_digest_snapshot(
                raw_root / raw_name,
                parent=raw_root,
                label="Historical capture evidence",
                max_bytes=MAX_CAPTURE_BYTES,
            )
            if source_format == "native_complete_v1":
                declared_size = payload.get("bytes_read")
                if (
                    isinstance(declared_size, bool)
                    or not isinstance(declared_size, int)
                    or declared_size < 0
                    or declared_size != raw_size
                ):
                    raise ValueError(
                        "Historical capture bytes_read does not match its file."
                    )
            modern_raw = (
                source_format == "native_complete_v1"
                and _MODERN_RAW_NAME.fullmatch(raw_name) is not None
            )
            raw_bytes_exact = modern_raw
            artifact_content_kind = (
                "exact_serial_bytes"
                if modern_raw
                else "legacy_utf8_replacement_text"
            )
            raw_logs = (
                log_repository.RawLogArtifact(
                    kind=(
                        "serial_capture_raw"
                        if modern_raw
                        else "serial_capture_legacy_text"
                    ),
                    path=f"raw/{raw_name}",
                    sha256=raw_sha256,
                ),
            )
        elif level not in {"error", "critical"}:
            raise ValueError(
                "Successful historical capture has no raw_path evidence."
            )

        errors = _historical_errors(
            payload,
            event_uuid=requested_event_uuid,
            level=level,
            event_message=message,
        )
        artifacts = log_repository.EventArtifacts(
            raw_logs=raw_logs,
            errors=errors,
        )
        if not artifacts.raw_logs and not artifacts.errors:
            raise ValueError(
                "Historical capture declares no recoverable completion evidence."
            )

        expected_status = (
            "failed" if level in {"error", "critical"} else "succeeded"
        )
        selected_port = record.get("selected_port")
        if not isinstance(selected_port, str) or not selected_port.strip():
            payload_port = payload.get("port")
            selected_port = (
                payload_port
                if isinstance(payload_port, str) and payload_port.strip()
                else None
            )
        event_profile = {
            "event_uuid": requested_event_uuid,
            "project_id": scope.project_id,
            "run_id": safe_run_id,
            "ts": timestamp,
            "phase": expected_phase,
            "level": level,
            "tool": tool,
            "source": source,
            "message": message,
            "payload_json": payload,
        }
        run_profile = {
            "project_id": scope.project_id,
            "run_id": safe_run_id,
            "task_type": expected_task_type,
            "selected_port": selected_port,
            "status": expected_status,
            "terminal_event_uuid": requested_event_uuid,
        }

        _sessions_after, _raw_after, directory_snapshot_after = (
            _directory_snapshot(scope)
        )
        if directory_snapshot_after != directory_snapshot:
            raise ValueError(
                "Historical capture directory chain changed during resolution."
            )
        eligible = source_format == "native_complete_v1"
        status = (
            "ineligible"
            if not eligible
            else "resolved"
            if artifacts.raw_logs or artifacts.errors
            else "no_artifacts"
        )
        event_profile_json = _canonical_json(event_profile)
        run_profile_json = _canonical_json(run_profile)
        return HistoricalSerialCaptureArtifactCandidate(
            status=status,
            adapter_id=HISTORICAL_CAPTURE_ADAPTER_ID,
            reconciliation_version=HISTORICAL_CAPTURE_RECONCILIATION_VERSION,
            source_format=source_format,
            project_id=scope.project_id,
            run_id=safe_run_id,
            requested_event_uuid=requested_event_uuid,
            source_path=f"sessions/{safe_source_name}",
            source_sha256=source_sha256,
            source_size=source_size,
            source_record_count=len(records),
            source_record_number=selected.number,
            source_record_sha256=selected.sha256,
            expected_event_phase=expected_phase,
            expected_run_status=expected_status,
            database_projection_eligible=eligible,
            database_projection_reason=(
                None if eligible else "legacy_event_phase_unknown"
            ),
            artifacts=artifacts,
            raw_size=raw_size,
            raw_bytes_exact=raw_bytes_exact,
            artifact_content_kind=artifact_content_kind,
            expected_event_profile_sha256=hashlib.sha256(
                event_profile_json.encode("utf-8")
            ).hexdigest(),
            artifact_bundle_sha256=_artifact_bundle_sha256(artifacts),
            _expected_event_profile_json=event_profile_json,
            _expected_run_profile_json=run_profile_json,
        )
    except HistoricalCaptureResolutionError:
        raise
    except Exception as exc:
        raise HistoricalCaptureResolutionError(
            "Historical capture artifacts could not be resolved: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
