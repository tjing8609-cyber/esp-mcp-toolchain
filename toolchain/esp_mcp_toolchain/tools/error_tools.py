from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
from typing import Annotated, Any

from pydantic import Field

from ..backends.serial_monitor_store import (
    SerialLogStoreError,
    _safe_binary_reader,
    read_manifest_snapshot,
    safe_directory_identity,
    verified_finalized_chunks,
)
from ..database import log_repository
from ..errors import execution_error
from ..utils.error_detection import MicroPythonErrorDetector, parse_error_text
from .log_tools import (
    ERROR_DETAIL_LIMIT,
    ERROR_EXCEPTION_TYPE_CHAR_LIMIT,
    ERROR_FILE_CHAR_LIMIT,
    ERROR_MESSAGE_CHAR_LIMIT,
    ERROR_RAW_TEXT_CHAR_LIMIT,
    RAW_LOG_DETAIL_LIMIT,
    LogScope,
)


MaxErrorScanBytes = Annotated[int, Field(ge=4096, le=1_048_576)]
LEGACY_EVENT_LIMIT = 64
LEGACY_EVENT_MESSAGE_CHAR_LIMIT = 8_192
LEGACY_EVENT_PAYLOAD_CHAR_LIMIT = 16_384
MAX_ARTIFACT_VERIFY_BYTES = 64 * 1024 * 1024
_MONITOR_CHUNK_PATTERN = re.compile(r"chunk-\d{6}\.bin")
_FORMAL_RAW_KINDS = {"serial_capture_raw", "serial_monitor_chunk"}


class _LogArtifactReadError(RuntimeError):
    def __init__(
        self,
        error_kind: str,
        message: str,
        *,
        recoverable: bool,
        raw_log_id: str | None = None,
        raw_log_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.recoverable = recoverable
        self.raw_log_id = raw_log_id
        self.raw_log_path = raw_log_path


def esp_error_parse_text(text: str) -> dict[str, Any]:
    parsed = parse_error_text(text)
    return {"ok": True, **parsed}


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload_json")
    if not isinstance(payload, dict):
        payload = event.get("data")
    return payload if isinstance(payload, dict) else {}


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _directory_chain(
    root: Path,
    parent: Path,
    *,
    label: str,
) -> tuple[list[Path], list[tuple[int, ...]]]:
    lexical_root = Path(os.path.abspath(root))
    lexical_parent = Path(os.path.abspath(parent))
    if not _is_within(lexical_parent, lexical_root):
        raise _LogArtifactReadError(
            "log_artifact_invalid",
            f"{label} parent escapes the active project's log root.",
            recoverable=False,
        )
    relative = lexical_parent.relative_to(lexical_root)
    paths = [lexical_root]
    current = lexical_root
    for part in relative.parts:
        current /= part
        paths.append(current)
    try:
        identities = [
            safe_directory_identity(
                path,
                label=f"{label} directory",
                include_metadata=False,
            )
            for path in paths
        ]
    except SerialLogStoreError as exc:
        raise _artifact_store_failure(exc, label=label) from exc
    return paths, identities


def _underlying_os_error(exc: BaseException) -> OSError | None:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, OSError):
            return current
        cause = current.__cause__
        current = cause if isinstance(cause, BaseException) else None
    return None


def _artifact_store_failure(
    exc: BaseException,
    *,
    label: str,
) -> _LogArtifactReadError:
    operating_error = _underlying_os_error(exc)
    unavailable_errnos = {
        errno.ENOENT,
        errno.EACCES,
        errno.EBUSY,
        errno.EMFILE,
        errno.ENFILE,
    }
    if (
        operating_error is not None
        and getattr(operating_error, "errno", None) in unavailable_errnos
    ):
        return _LogArtifactReadError(
            "log_artifact_unavailable",
            f"{label} is temporarily unavailable: {operating_error}",
            recoverable=True,
        )
    return _LogArtifactReadError(
        "log_artifact_invalid",
        f"{label} could not be read safely: {exc}",
        recoverable=False,
    )


def _read_safe_artifact(
    *,
    log_root: Path,
    relative_path: str,
    expected_sha256: str | None,
    capture_limit: int,
    label: str,
) -> tuple[bytes, int, str]:
    if capture_limit < 0:
        raise ValueError("capture_limit must not be negative")
    relative = PurePosixPath(relative_path)
    parts = relative.parts
    if (
        not parts
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or relative.as_posix() != relative_path
    ):
        raise _LogArtifactReadError(
            "log_artifact_invalid",
            f"{label} has an invalid relative path.",
            recoverable=False,
        )
    if expected_sha256 is None:
        raise _LogArtifactReadError(
            "log_artifact_invalid",
            f"{label} has no authoritative sha256.",
            recoverable=False,
        )

    lexical_root = Path(os.path.abspath(log_root))
    candidate = lexical_root.joinpath(*parts)
    paths, identities = _directory_chain(
        lexical_root,
        candidate.parent,
        label=label,
    )
    digest = hashlib.sha256()
    total_bytes = 0
    captured = bytearray()
    try:
        with _safe_binary_reader(
            candidate,
            parent=candidate.parent,
            label=label,
        ) as (handle, status):
            if status.st_size > MAX_ARTIFACT_VERIFY_BYTES:
                raise _LogArtifactReadError(
                    "log_artifact_invalid",
                    f"{label} exceeds the {MAX_ARTIFACT_VERIFY_BYTES}-byte "
                    "verification limit.",
                    recoverable=False,
                )
            while block := handle.read(1024 * 1024):
                digest.update(block)
                total_bytes += len(block)
                remaining = capture_limit - len(captured)
                if remaining > 0:
                    captured.extend(block[:remaining])
    except (OSError, SerialLogStoreError) as exc:
        raise _artifact_store_failure(exc, label=label) from exc

    try:
        identities_after = [
            safe_directory_identity(
                path,
                label=f"{label} directory",
                include_metadata=False,
            )
            for path in paths
        ]
    except SerialLogStoreError as exc:
        raise _artifact_store_failure(exc, label=label) from exc
    if identities_after != identities:
        raise _LogArtifactReadError(
            "log_artifact_invalid",
            f"{label} directory chain changed during verification.",
            recoverable=False,
        )

    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256.lower():
        raise _LogArtifactReadError(
            "log_artifact_invalid",
            f"{label} sha256 does not match the registered digest.",
            recoverable=False,
        )
    return bytes(captured), total_bytes, actual_sha256


def _registered_raw_path(record: dict[str, Any], run_id: str) -> str | None:
    kind = str(record.get("kind") or "")
    if kind not in _FORMAL_RAW_KINDS:
        return None
    raw_path = str(record.get("path") or "")
    parts = PurePosixPath(raw_path).parts
    if kind == "serial_capture_raw":
        valid = len(parts) == 2 and parts[0] == "raw"
    else:
        valid = (
            len(parts) == 3
            and parts[0] == "serial"
            and parts[1] == run_id
            and _MONITOR_CHUNK_PATTERN.fullmatch(parts[2]) is not None
        )
    if not valid:
        raise _LogArtifactReadError(
            "log_artifact_invalid",
            f"Registered {kind} path does not match its artifact kind and run.",
            recoverable=False,
        )
    return raw_path


def _formal_error_report(record: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_error_text(str(record.get("raw_text") or ""))
    for field in ("file", "line", "exception_type", "message"):
        value = record.get(field)
        if value is not None:
            parsed[field] = value
    parsed.update(
        {
            "has_error": True,
            "error_kind": record.get("error_kind"),
            "column": record.get("column"),
            "recoverable": record.get("recoverable"),
        }
    )
    if not parsed.get("suggested_next_actions"):
        parsed["suggested_next_actions"] = [
            "Open the related source file",
            "Fix the reported exception",
            "Upload or build again",
            "Run again and capture serial output",
        ]
    return parsed


def _query_source(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "sqlite",
        "schema_version": snapshot["schema_version"],
        "authoritative": True,
    }


def _success_result(
    *,
    run_id: str,
    snapshot: dict[str, Any],
    scan_sources: list[dict[str, Any]],
    skipped_sources: list[dict[str, str]],
    scanned_bytes: int,
    scan_truncated: bool,
    report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "run_id": run_id,
        "query_source": _query_source(snapshot),
        "scan_sources": scan_sources,
        "skipped_sources": skipped_sources,
        "scanned_bytes": scanned_bytes,
        "scan_truncated": scan_truncated,
        "source_truncation": {
            "raw_logs": bool(snapshot.get("raw_logs_truncated")),
            "errors": bool(snapshot.get("errors_truncated")),
            "error_fields": bool(snapshot.get("error_fields_truncated")),
            "legacy_events": bool(snapshot.get("legacy_events_truncated")),
        },
        **report,
    }


def _read_formal_raw_logs(
    *,
    scope: LogScope,
    run_id: str,
    raw_logs: list[dict[str, Any]],
    max_bytes: int,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, str]], int, bool] | None:
    eligible: list[tuple[dict[str, Any], str]] = []
    skipped_sources: list[dict[str, str]] = []
    for record in raw_logs:
        try:
            relative_path = _registered_raw_path(record, run_id)
        except _LogArtifactReadError as exc:
            exc.raw_log_id = record.get("raw_log_id")
            exc.raw_log_path = record.get("path")
            raise
        if relative_path is None:
            skipped_sources.append(
                {
                    "kind": "sqlite_raw_log",
                    "reason": f"unsupported_kind:{record.get('kind')}",
                }
            )
            continue
        eligible.append((record, relative_path))
    if not eligible:
        return None

    chunks: list[str] = []
    scan_sources: list[dict[str, Any]] = []
    scanned_bytes = 0
    scan_truncated = False
    for index, (record, relative_path) in enumerate(eligible):
        remaining = max_bytes - scanned_bytes
        if remaining <= 0:
            scan_truncated = True
            break
        try:
            payload, total_bytes, _actual_sha256 = _read_safe_artifact(
                log_root=scope.log_root,
                relative_path=relative_path,
                expected_sha256=record.get("sha256"),
                capture_limit=remaining,
                label=f"Registered raw log {record.get('raw_log_id')}",
            )
        except _LogArtifactReadError as exc:
            exc.raw_log_id = record.get("raw_log_id")
            exc.raw_log_path = relative_path
            raise
        chunks.append(payload.decode("utf-8", errors="replace"))
        scanned_bytes += len(payload)
        if total_bytes > len(payload) or index + 1 < len(eligible) and scanned_bytes >= max_bytes:
            scan_truncated = True
        scan_sources.append(
            {
                "kind": "sqlite_raw_log",
                "raw_log_id": record["raw_log_id"],
                "artifact_kind": record["kind"],
                "path": relative_path,
                "bytes": len(payload),
                "sha256_verified": True,
            }
        )
    return chunks, scan_sources, skipped_sources, scanned_bytes, scan_truncated


def _legacy_raw_relative_path(raw_value: str, log_root: Path) -> str:
    lexical_root = Path(os.path.abspath(log_root))
    supplied = Path(raw_value)
    if supplied.is_absolute():
        candidate = Path(os.path.abspath(supplied))
    else:
        if (
            "\\" in raw_value
            or ":" in raw_value
            or any(part in {"", ".", ".."} for part in PurePosixPath(raw_value).parts)
        ):
            raise ValueError("legacy raw path is not a safe relative POSIX path")
        candidate = lexical_root.joinpath(*PurePosixPath(raw_value).parts)
    if not _is_within(candidate, lexical_root):
        raise ValueError("legacy raw path is outside the active project log root")
    return candidate.relative_to(lexical_root).as_posix()


def _read_legacy_raw(
    *,
    scope: LogScope,
    relative_path: str,
    capture_limit: int,
) -> tuple[bytes, int, str]:
    lexical_root = Path(os.path.abspath(scope.log_root))
    candidate = lexical_root.joinpath(*PurePosixPath(relative_path).parts)
    paths, identities = _directory_chain(
        lexical_root,
        candidate.parent,
        label="Legacy raw log",
    )
    digest = hashlib.sha256()
    captured = bytearray()
    total_bytes = 0
    try:
        with _safe_binary_reader(
            candidate,
            parent=candidate.parent,
            label="Legacy raw log",
        ) as (handle, status):
            if status.st_size > MAX_ARTIFACT_VERIFY_BYTES:
                raise _LogArtifactReadError(
                    "log_artifact_invalid",
                    "Legacy raw log exceeds the bounded verification limit.",
                    recoverable=False,
                )
            while block := handle.read(1024 * 1024):
                digest.update(block)
                total_bytes += len(block)
                remaining = capture_limit - len(captured)
                if remaining > 0:
                    captured.extend(block[:remaining])
    except (OSError, SerialLogStoreError) as exc:
        raise _artifact_store_failure(exc, label="Legacy raw log") from exc
    try:
        identities_after = [
            safe_directory_identity(
                path,
                label="Legacy raw log directory",
                include_metadata=False,
            )
            for path in paths
        ]
    except SerialLogStoreError as exc:
        raise _artifact_store_failure(exc, label="Legacy raw log") from exc
    if identities_after != identities:
        raise _LogArtifactReadError(
            "log_artifact_invalid",
            "Legacy raw log directory chain changed while it was read.",
            recoverable=False,
        )
    return bytes(captured), total_bytes, digest.hexdigest()


def _append_legacy_monitor(
    *,
    scope: LogScope,
    run_id: str,
    max_bytes: int,
    scanned_bytes: int,
    chunks: list[str],
    scan_sources: list[dict[str, Any]],
    skipped_sources: list[dict[str, str]],
) -> tuple[int, bool]:
    if (
        not run_id
        or Path(run_id).name != run_id
        or "\\" in run_id
        or "/" in run_id
        or ":" in run_id
        or run_id in {".", ".."}
    ):
        skipped_sources.append(
            {"kind": "serial_monitor", "reason": "invalid_run_id_for_compatibility"}
        )
        return scanned_bytes, False
    run_dir = scope.log_root / "serial" / run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file() or scanned_bytes >= max_bytes:
        return scanned_bytes, scanned_bytes >= max_bytes and manifest_path.is_file()

    try:
        monitor_paths, monitor_identities = _directory_chain(
            scope.log_root,
            run_dir,
            label="Legacy Monitor",
        )
        manifest, _manifest_sha256 = read_manifest_snapshot(run_dir)
        if manifest.get("run_id") != run_id:
            raise SerialLogStoreError(
                "Monitor manifest run_id does not match the requested run."
            )
        manifest_chunks = manifest.get("chunks")
        if not isinstance(manifest_chunks, list):
            raise SerialLogStoreError("Monitor manifest chunks must be a list.")
        declared_bytes = 0
        for chunk in manifest_chunks:
            if not isinstance(chunk, dict):
                raise SerialLogStoreError(
                    "Monitor manifest chunk metadata must be an object."
                )
            byte_length = chunk.get("byte_length")
            if (
                isinstance(byte_length, bool)
                or not isinstance(byte_length, int)
                or byte_length < 0
            ):
                raise SerialLogStoreError(
                    "Monitor manifest chunk byte_length is invalid."
                )
            declared_bytes += byte_length
            if declared_bytes > MAX_ARTIFACT_VERIFY_BYTES:
                raise SerialLogStoreError(
                    "Legacy Monitor chunks exceed the bounded verification limit."
                )
        verified = verified_finalized_chunks(scope.log_root, manifest)
        monitor_chunks: list[str] = []
        monitor_bytes = 0
        monitor_truncated = False
        for index, artifact in enumerate(verified):
            remaining = max_bytes - scanned_bytes - monitor_bytes
            if remaining <= 0:
                monitor_truncated = True
                break
            payload, total_bytes, _actual_sha256 = _read_safe_artifact(
                log_root=scope.log_root,
                relative_path=artifact["path"],
                expected_sha256=artifact["sha256"],
                capture_limit=remaining,
                label=f"Legacy Monitor chunk {artifact['chunk_id']}",
            )
            monitor_chunks.append(payload.decode("utf-8", errors="replace"))
            monitor_bytes += len(payload)
            if (
                total_bytes > len(payload)
                or index + 1 < len(verified)
                and scanned_bytes + monitor_bytes >= max_bytes
            ):
                monitor_truncated = True
        monitor_identities_after = [
            safe_directory_identity(
                path,
                label="Legacy Monitor directory",
                include_metadata=False,
            )
            for path in monitor_paths
        ]
        if monitor_identities_after != monitor_identities:
            raise SerialLogStoreError(
                "Legacy Monitor directory chain changed while it was read."
            )
        chunks.extend(monitor_chunks)
        scanned_bytes += monitor_bytes
        if monitor_bytes:
            scan_sources.append(
                {
                    "kind": "serial_monitor_raw",
                    "path": str(run_dir),
                    "bytes": monitor_bytes,
                    "compatibility": True,
                    "sha256_verified": True,
                }
            )
        return scanned_bytes, monitor_truncated
    except (OSError, SerialLogStoreError, _LogArtifactReadError, KeyError, TypeError, ValueError) as exc:
        skipped_sources.append(
            {"kind": "serial_monitor", "reason": f"{type(exc).__name__}: {exc}"}
        )
        return scanned_bytes, False


def esp_error_parse_log(run_id: str, max_bytes: MaxErrorScanBytes = 262_144) -> dict[str, Any]:
    if max_bytes < 4096 or max_bytes > 1_048_576:
        return execution_error(
            "invalid_max_bytes",
            "max_bytes must be between 4096 and 1048576.",
            tool="esp_error_parse_log",
        )

    scope = LogScope.active()
    try:
        snapshot = log_repository.read_error_parse_snapshot(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            raw_log_limit=RAW_LOG_DETAIL_LIMIT,
            error_limit=ERROR_DETAIL_LIMIT,
            error_file_char_limit=ERROR_FILE_CHAR_LIMIT,
            error_exception_type_char_limit=ERROR_EXCEPTION_TYPE_CHAR_LIMIT,
            error_message_char_limit=ERROR_MESSAGE_CHAR_LIMIT,
            error_raw_text_char_limit=ERROR_RAW_TEXT_CHAR_LIMIT,
            legacy_event_limit=LEGACY_EVENT_LIMIT,
            legacy_message_char_limit=min(
                max_bytes,
                LEGACY_EVENT_MESSAGE_CHAR_LIMIT,
            ),
            legacy_payload_char_limit=LEGACY_EVENT_PAYLOAD_CHAR_LIMIT,
        )
    except FileNotFoundError:
        snapshot = {
            "schema_version": None,
            "run": None,
            "raw_logs": [],
            "errors": [],
            "legacy_events": [],
        }
    except log_repository.LogDatabaseQueryError as exc:
        return execution_error(
            exc.error_kind,
            str(exc),
            tool="esp_error_parse_log",
            recoverable=exc.recoverable,
        )

    if snapshot["run"] is None:
        return {
            "ok": False,
            "error_kind": "run_not_found",
            "message": f"No log for run_id {run_id} in the active project",
        }

    errors = snapshot["errors"]
    if errors:
        latest = errors[-1]
        return _success_result(
            run_id=run_id,
            snapshot=snapshot,
            scan_sources=[
                {
                    "kind": "sqlite_errors",
                    "count": len(errors),
                    "error_id": latest["error_id"],
                    "bytes": 0,
                }
            ],
            skipped_sources=[],
            scanned_bytes=0,
            scan_truncated=False,
            report=_formal_error_report(latest),
        )

    try:
        formal_raw = _read_formal_raw_logs(
            scope=scope,
            run_id=run_id,
            raw_logs=snapshot["raw_logs"],
            max_bytes=max_bytes,
        )
    except _LogArtifactReadError as exc:
        return execution_error(
            exc.error_kind,
            str(exc),
            tool="esp_error_parse_log",
            recoverable=exc.recoverable,
            run_id=run_id,
            raw_log_id=exc.raw_log_id,
            raw_log_path=exc.raw_log_path,
        )
    if formal_raw is not None:
        (
            chunks,
            scan_sources,
            skipped_sources,
            scanned_bytes,
            scan_truncated,
        ) = formal_raw
        return _success_result(
            run_id=run_id,
            snapshot=snapshot,
            scan_sources=scan_sources,
            skipped_sources=skipped_sources,
            scanned_bytes=scanned_bytes,
            scan_truncated=(
                scan_truncated or bool(snapshot.get("raw_logs_truncated"))
            ),
            report=parse_error_text("\n".join(chunks)),
        )

    chunks: list[str] = []
    scan_sources: list[dict[str, Any]] = []
    skipped_sources: list[dict[str, str]] = []
    structured_reports: list[dict[str, Any]] = []
    scanned_bytes = 0
    scan_truncated = bool(snapshot.get("legacy_events_truncated"))
    legacy_events = snapshot["legacy_events"]

    for event in legacy_events:
        payload = _event_payload(event)
        report = payload.get("error_report")
        if (
            not isinstance(report, dict)
            and payload.get("has_error") is True
            and payload.get("error_kind")
        ):
            report = payload
        if isinstance(report, dict) and report.get("has_error") is True:
            structured_reports.append(report)
        truncation = event.get("field_truncation")
        if isinstance(truncation, dict) and any(bool(value) for value in truncation.values()):
            scan_truncated = True

    event_text = "\n".join(
        event["message"]
        for event in legacy_events
        if isinstance(event.get("message"), str) and event["message"]
    )
    if event_text:
        encoded = event_text.encode("utf-8")
        bounded = encoded[:max_bytes]
        chunks.append(bounded.decode("utf-8", errors="replace"))
        scanned_bytes += len(bounded)
        scan_truncated = scan_truncated or len(encoded) > len(bounded)
        scan_sources.append(
            {
                "kind": "sqlite_events",
                "bytes": len(bounded),
                "compatibility": True,
            }
        )

    raw_candidates: list[str] = []
    for event in legacy_events:
        raw_value = _event_payload(event).get("raw_path")
        if not isinstance(raw_value, str) or not raw_value:
            continue
        try:
            relative_path = _legacy_raw_relative_path(raw_value, scope.log_root)
        except ValueError as exc:
            skipped_sources.append({"kind": "raw_path", "reason": str(exc)})
            continue
        if relative_path not in raw_candidates:
            raw_candidates.append(relative_path)

    for relative_path in raw_candidates:
        remaining = max_bytes - scanned_bytes
        if remaining <= 0:
            scan_truncated = True
            break
        try:
            payload, total_bytes, computed_sha256 = _read_legacy_raw(
                scope=scope,
                relative_path=relative_path,
                capture_limit=remaining,
            )
        except _LogArtifactReadError as exc:
            skipped_sources.append(
                {"kind": "raw_path", "reason": f"{exc.error_kind}: {exc}"}
            )
            continue
        chunks.append(payload.decode("utf-8", errors="replace"))
        scanned_bytes += len(payload)
        scan_truncated = scan_truncated or total_bytes > len(payload)
        scan_sources.append(
            {
                "kind": "serial_capture_raw",
                "path": str(scope.log_root / relative_path),
                "bytes": len(payload),
                "compatibility": True,
                "sha256_computed": computed_sha256,
            }
        )

    monitor_start_bytes = scanned_bytes
    scanned_bytes, monitor_truncated = _append_legacy_monitor(
        scope=scope,
        run_id=run_id,
        max_bytes=max_bytes,
        scanned_bytes=scanned_bytes,
        chunks=chunks,
        scan_sources=scan_sources,
        skipped_sources=skipped_sources,
    )
    scan_truncated = scan_truncated or monitor_truncated
    if scanned_bytes >= max_bytes and scanned_bytes > monitor_start_bytes:
        scan_truncated = True

    parsed = parse_error_text("\n".join(chunks))
    candidates = [parsed, *structured_reports]
    parsed = max(
        candidates,
        key=lambda report: (
            int(bool(report.get("has_error"))),
            int(bool(report.get("exception_type"))),
            int(bool(report.get("file"))),
            int(report.get("line") is not None),
        ),
    )
    if structured_reports:
        scan_sources.append(
            {
                "kind": "structured_error_report",
                "count": len(structured_reports),
                "bytes": 0,
                "compatibility": True,
            }
        )
    return _success_result(
        run_id=run_id,
        snapshot=snapshot,
        scan_sources=scan_sources,
        skipped_sources=skipped_sources,
        scanned_bytes=scanned_bytes,
        scan_truncated=scan_truncated,
        report=parsed,
    )


__all__ = [
    "MicroPythonErrorDetector",
    "esp_error_parse_log",
    "esp_error_parse_text",
    "parse_error_text",
]
