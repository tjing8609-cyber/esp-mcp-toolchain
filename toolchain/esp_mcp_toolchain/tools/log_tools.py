from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile
from threading import RLock
from typing import Any, Callable, TypeVar
from uuid import uuid4

from ..config import get_selected_port
from ..database import log_repository
from ..database.event_repository import EventRepositoryError, normalize_timestamp
from ..database.migrations import init_database
from ..errors import execution_error
from ..paths import logs_dir
from ..project_context import get_project_context
from ..store.jsonl_store import append_jsonl, read_jsonl
from ..utils.time_utils import now_compact, now_iso


F = TypeVar("F", bound=Callable[..., dict[str, Any]])
_PREPARED_DATABASES: set[tuple[str, str]] = set()
_PREPARE_LOCK = RLock()
_MIRROR_LOCK = RLock()
_TASK_RUN_CONTEXT: ContextVar[tuple[str, "LogScope"] | None] = ContextVar(
    "esp_mcp_task_run_context",
    default=None,
)
_RESULT_LOG_KEYS = {
    "backend",
    "mode",
    "target",
    "port",
    "baud",
    "baudrate",
    "bytes_read",
    "bytes_written",
    "error_kind",
    "implemented",
    "recoverable",
    "raw_path",
    "session_name",
    "state",
    "has_error",
    "error_report",
    "interrupt_sent",
    "stop_confirmed",
    "serial_opened",
    "control_lines_preconfigured",
    "reset_command_sent",
    "hard_reset_pulse_started",
    "hard_reset_pulse_completed",
    "hard_reset_line_released",
    "reset_confirmed",
    "physical_reset_excluded",
    "pre_action_window_ms",
    "pre_action_bytes_read",
    "pre_action_output_observed",
    "pre_action_capture_limit_reached",
    "pre_action_text",
    "output_capture_limit_reached",
    "output_causality_confirmed",
    "cleanup_required",
    "cleanup_attempted",
    "cleanup_completed",
    "cleanup_errors",
    "failure_stage",
    "program_interrupted",
    "execution_confirmed",
    "repeated_execution_confirmed",
    "gpio_read_only",
    "passed",
    "failed",
    "skipped",
    "duration_us",
    "profile_kind",
    "iterations",
    "failed_count",
}
_COMPLETION_ARTIFACT_POLICIES = {
    "serial_capture_raw",
    "result_error",
    "structured_error",
}


class CompletionArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class LogScope:
    project_id: str
    project_dir: Path
    log_root: Path
    database_file: Path

    @classmethod
    def active(cls) -> "LogScope":
        context = get_project_context()
        project_dir = Path(context["project_dir"])
        return cls(
            project_id=str(context["project_id"]),
            project_dir=project_dir,
            log_root=project_dir / "logs",
            database_file=project_dir / "esp_mcp.sqlite",
        )

    @classmethod
    def bound(cls, *, project_id: str, log_root: str | Path) -> "LogScope":
        resolved_log_root = Path(log_root)
        project_dir = resolved_log_root.parent
        return cls(
            project_id=project_id,
            project_dir=project_dir,
            log_root=resolved_log_root,
            database_file=project_dir / "esp_mcp.sqlite",
        )


def resolve_log_scope(
    *,
    scope: LogScope | None = None,
    project_id: str | None = None,
    log_root: str | Path | None = None,
) -> LogScope:
    if scope is not None:
        return scope
    if log_root is not None:
        resolved_project_id = project_id or str(get_project_context()["project_id"])
        return LogScope.bound(project_id=resolved_project_id, log_root=log_root)
    return LogScope.active()


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


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


def _verified_serial_capture_artifact(
    result: dict[str, Any],
    scope: LogScope,
) -> log_repository.RawLogArtifact:
    raw_path = result.get("raw_path")
    bytes_read = result.get("bytes_read")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise CompletionArtifactError("serial capture raw_path is missing")
    if isinstance(bytes_read, bool) or not isinstance(bytes_read, int) or bytes_read < 0:
        raise CompletionArtifactError("serial capture bytes_read is invalid")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise CompletionArtifactError("serial capture raw_path must be absolute")

    raw_root = scope.log_root / "raw"
    try:
        canonical_root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise CompletionArtifactError(
            f"serial capture raw root is unavailable: {exc}"
        ) from exc
    if _is_reparse_point(raw_root):
        raise CompletionArtifactError(
            "serial capture raw root must not be a symbolic link or reparse point"
        )

    lexical_candidate = Path(os.path.abspath(candidate))
    if not _is_within(lexical_candidate, canonical_root):
        raise CompletionArtifactError(
            "serial capture raw_path is outside the active project's raw log root"
        )
    relative = lexical_candidate.relative_to(canonical_root)
    current = canonical_root
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            raise CompletionArtifactError(
                "serial capture raw_path contains a symbolic link or reparse point"
            )
    try:
        canonical_candidate = lexical_candidate.resolve(strict=True)
    except OSError as exc:
        raise CompletionArtifactError(
            f"serial capture raw_path is unavailable: {exc}"
        ) from exc
    if not _is_within(canonical_candidate, canonical_root):
        raise CompletionArtifactError(
            "serial capture raw_path resolves outside the active project's raw log root"
        )

    digest = hashlib.sha256()
    hashed_bytes = 0
    try:
        with canonical_candidate.open("rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise CompletionArtifactError(
                    "serial capture raw_path must identify a regular file"
                )
            if metadata.st_size != bytes_read:
                raise CompletionArtifactError(
                    "serial capture raw file size does not match bytes_read"
                )
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                hashed_bytes += len(chunk)
            metadata_after = os.fstat(handle.fileno())
            if (
                hashed_bytes != bytes_read
                or metadata_after.st_size != bytes_read
                or metadata_after.st_dev != metadata.st_dev
                or metadata_after.st_ino != metadata.st_ino
            ):
                raise CompletionArtifactError(
                    "serial capture raw file changed during verification"
                )
    except CompletionArtifactError:
        raise
    except OSError as exc:
        raise CompletionArtifactError(
            f"serial capture raw file could not be verified: {exc}"
        ) from exc
    if _is_reparse_point(lexical_candidate):
        raise CompletionArtifactError(
            "serial capture raw_path became a symbolic link or reparse point"
        )
    stored_path = canonical_candidate.relative_to(scope.log_root.resolve()).as_posix()
    return log_repository.RawLogArtifact(
        kind="serial_capture_raw",
        path=stored_path,
        sha256=digest.hexdigest(),
    )


def _result_error_artifact(
    result: dict[str, Any],
    event_uuid: str,
) -> log_repository.ErrorArtifact | None:
    if result.get("ok") is not False:
        return None
    error_kind = _optional_text(result.get("error_kind"))
    if error_kind is None:
        return None
    return log_repository.ErrorArtifact(
        occurrence_key=f"event:{event_uuid}:result_error",
        error_kind=error_kind,
        file=_optional_text(result.get("file")),
        line=_optional_positive_integer(result.get("line")),
        column=_optional_positive_integer(result.get("column")),
        exception_type=_optional_text(result.get("exception_type")),
        message=None if result.get("message") is None else str(result.get("message")),
        raw_text=None,
        recoverable=_optional_recoverable(result.get("recoverable")),
    )


def _structured_error_artifact(
    result: dict[str, Any],
    event_uuid: str,
) -> log_repository.ErrorArtifact | None:
    report = result.get("error_report")
    if (
        result.get("has_error") is not True
        or not isinstance(report, dict)
        or report.get("has_error") is not True
    ):
        return None
    error_kind = _optional_text(report.get("error_kind"))
    if error_kind is None:
        return None
    return log_repository.ErrorArtifact(
        occurrence_key=f"event:{event_uuid}:structured_error",
        error_kind=error_kind,
        file=_optional_text(report.get("file")),
        line=_optional_positive_integer(report.get("line")),
        column=_optional_positive_integer(report.get("column")),
        exception_type=_optional_text(report.get("exception_type")),
        message=None if report.get("message") is None else str(report.get("message")),
        raw_text=None,
        recoverable=_optional_recoverable(report.get("recoverable")),
    )


def _build_completion_artifacts(
    *,
    result: dict[str, Any],
    event_uuid: str,
    scope: LogScope,
    policies: tuple[str, ...],
) -> log_repository.EventArtifacts:
    raw_logs: list[log_repository.RawLogArtifact] = []
    errors: list[log_repository.ErrorArtifact] = []
    if "serial_capture_raw" in policies and result.get("ok") is True:
        raw_logs.append(_verified_serial_capture_artifact(result, scope))
    if "result_error" in policies:
        result_error = _result_error_artifact(result, event_uuid)
        if result_error is not None:
            errors.append(result_error)
    if "structured_error" in policies:
        structured_error = _structured_error_artifact(result, event_uuid)
        if structured_error is not None:
            errors.append(structured_error)
    return log_repository.EventArtifacts(
        raw_logs=tuple(raw_logs),
        errors=tuple(errors),
    )


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}_{now_compact()}_{uuid4().hex[:8]}"


def _logs_root(log_root: str | Path | None = None) -> Path:
    return Path(log_root) if log_root is not None else logs_dir()


def latest_path(log_root: str | Path | None = None) -> Path:
    return _logs_root(log_root) / "latest.json"


def session_path(run_id: str, log_root: str | Path | None = None) -> Path:
    return _logs_root(log_root) / "sessions" / f"{run_id}.jsonl"


def _prepare_scope(scope: LogScope, *, force_import: bool = False) -> dict[str, Any]:
    key = (str(scope.database_file.resolve()), scope.project_id)
    with _PREPARE_LOCK:
        if key in _PREPARED_DATABASES and not force_import:
            return {"files_imported": 0, "events_imported": 0, "events_deduplicated": 0}
        scope.log_root.mkdir(parents=True, exist_ok=True)
        (scope.log_root / "sessions").mkdir(parents=True, exist_ok=True)
        init_database(scope.database_file, project_id=scope.project_id)
        report = log_repository.import_jsonl_sessions(
            scope.database_file,
            project_id=scope.project_id,
            logs_root=scope.log_root,
        )
        _PREPARED_DATABASES.add(key)
        return report


def start_run(
    task_type: str,
    *,
    run_id: str | None = None,
    selected_port: str | None = None,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
    scope: LogScope | None = None,
    project_id: str | None = None,
    log_root: str | Path | None = None,
) -> dict[str, Any]:
    resolved_scope = resolve_log_scope(scope=scope, project_id=project_id, log_root=log_root)
    _prepare_scope(resolved_scope)
    rid = run_id or new_run_id(task_type.removeprefix("esp_") or "run")
    run, created = log_repository.create_run(
        resolved_scope.database_file,
        project_id=resolved_scope.project_id,
        run_id=rid,
        task_type=task_type,
        started_at=now_iso(),
        selected_port=selected_port,
        summary=summary,
        payload=payload,
    )
    return {**run, "created": created}


def finish_run(
    run_id: str,
    status: str,
    *,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
    scope: LogScope | None = None,
    project_id: str | None = None,
    log_root: str | Path | None = None,
) -> dict[str, Any]:
    resolved_scope = resolve_log_scope(scope=scope, project_id=project_id, log_root=log_root)
    _prepare_scope(resolved_scope)
    run = log_repository.finish_run(
        resolved_scope.database_file,
        project_id=resolved_scope.project_id,
        run_id=run_id,
        status=status,
        ended_at=now_iso(),
        summary=summary,
        payload=payload,
    )
    try:
        _write_latest_mirror(run, resolved_scope)
    except Exception as exc:
        run["logging_persisted"] = False
        run["logging_warning"] = f"latest mirror: {type(exc).__name__}: {exc}"
    return run


def _event_is_mirrored(path: Path, expected: dict[str, Any]) -> bool:
    if not path.exists():
        return False
    event_uuid = str(expected["event_uuid"])
    matches = [
        row
        for row in read_jsonl(path)
        if (
            row.get("event_uuid") == event_uuid
            or row.get("event_id") == event_uuid
        )
    ]
    if not matches:
        return False
    if len(matches) != 1 or matches[0] != expected:
        raise ValueError(
            f"Session mirror conflicts with event_uuid {event_uuid}."
        )
    return True


def _event_mirror_record(
    event: dict[str, Any],
    scope: LogScope,
) -> dict[str, Any]:
    mirror = dict(event)
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=event["run_id"],
    )
    if run is not None:
        mirror["task_type"] = run["task_type"]
        mirror["selected_port"] = run["selected_port"]
    return mirror


def _mirror_event(event: dict[str, Any], scope: LogScope) -> None:
    with _MIRROR_LOCK:
        path = session_path(event["run_id"], scope.log_root)
        mirror = _event_mirror_record(event, scope)
        if _event_is_mirrored(path, mirror):
            return
        append_jsonl(path, mirror)


def _write_latest_mirror(run: dict[str, Any], scope: LogScope) -> None:
    with _MIRROR_LOCK:
        latest = _latest_mirror_record(run, scope)
        target = latest_path(scope.log_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and _is_reparse_point(target):
            raise OSError("latest.json is a symbolic link or reparse point")
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=target.parent,
                prefix=".latest.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_name = handle.name
                json.dump(latest, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass


def _latest_mirror_record(
    run: dict[str, Any],
    scope: LogScope,
) -> dict[str, Any]:
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        tail=1,
    )
    last_event = events[-1] if events else None
    return {
        "project_id": scope.project_id,
        "run_id": run["run_id"],
        "task_type": run["task_type"],
        "status": run["status"],
        "last_tool": last_event["tool"] if last_event else run["task_type"],
        "has_error": run["status"] == "failed",
        "summary": run.get("summary")
        or (last_event["message"] if last_event else None),
        "updated_at": run.get("ended_at") or run["started_at"],
    }


def committed_event_and_latest_mirrors_match(
    event: dict[str, Any],
    scope: LogScope,
) -> bool:
    with _MIRROR_LOCK:
        expected_event = _event_mirror_record(event, scope)
        if not _event_is_mirrored(
            session_path(event["run_id"], scope.log_root),
            expected_event,
        ):
            return False
        latest_run = log_repository.latest_run(
            scope.database_file,
            project_id=scope.project_id,
        )
        if latest_run is None:
            return False
        target = latest_path(scope.log_root)
        if not target.exists() or _is_reparse_point(target):
            return False
        stored_latest = json.loads(target.read_text(encoding="utf-8"))
        return stored_latest == _latest_mirror_record(latest_run, scope)


def mirror_committed_event_and_refresh_latest(
    event: dict[str, Any],
    scope: LogScope,
) -> dict[str, Any]:
    warnings: list[str] = []
    session_persisted = False
    latest_persisted = False
    try:
        _mirror_event(event, scope)
        session_persisted = True
    except Exception as exc:
        warnings.append(f"session mirror: {type(exc).__name__}: {exc}")
    try:
        latest = log_repository.latest_run(
            scope.database_file,
            project_id=scope.project_id,
        )
        if latest is not None:
            _write_latest_mirror(latest, scope)
        latest_persisted = True
    except Exception as exc:
        warnings.append(f"latest mirror: {type(exc).__name__}: {exc}")
    return {
        "ok": not warnings,
        "session_persisted": session_persisted,
        "latest_persisted": latest_persisted,
        "warnings": warnings,
    }


def write_event(
    tool: str,
    level: str,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
    ts: str | None = None,
    phase: str = "execute",
    event_uuid: str | None = None,
    source: str = "toolchain",
    task_type: str | None = None,
    selected_port: str | None = None,
    auto_finish: bool | None = None,
    artifacts: log_repository.EventArtifacts | None = None,
    scope: LogScope | None = None,
    project_id: str | None = None,
    log_root: str | Path | None = None,
) -> dict[str, Any]:
    resolved_scope = resolve_log_scope(scope=scope, project_id=project_id, log_root=log_root)
    _prepare_scope(resolved_scope)
    generated_run = run_id is None
    rid = run_id or new_run_id("run")
    payload = data or {}
    inferred_port = selected_port or (payload.get("port") if isinstance(payload.get("port"), str) else None)
    try:
        if log_repository.get_run(
            resolved_scope.database_file,
            project_id=resolved_scope.project_id,
            run_id=rid,
        ) is None:
            log_repository.create_run(
                resolved_scope.database_file,
                project_id=resolved_scope.project_id,
                run_id=rid,
                task_type=task_type or tool,
                started_at=now_iso(),
                selected_port=inferred_port,
                summary=message,
                payload={},
            )
        append_arguments = {
            "database": resolved_scope.database_file,
            "project_id": resolved_scope.project_id,
            "run_id": rid,
            "event_uuid": event_uuid,
            "ts": ts or now_iso(),
            "phase": phase,
            "level": level,
            "tool": tool,
            "source": source,
            "message": message,
            "payload": payload,
        }
        if artifacts is None:
            event, inserted = log_repository.append_event(**append_arguments)
        else:
            report = log_repository.append_event_with_artifacts(
                **append_arguments,
                artifacts=artifacts,
            )
            event = report["event"]
            inserted = report["event_inserted"]
        event["deduplicated"] = not inserted
        logging_warnings: list[str] = []
        try:
            _mirror_event(event, resolved_scope)
        except Exception as exc:
            logging_warnings.append(f"session mirror: {type(exc).__name__}: {exc}")
        should_finish = generated_run if auto_finish is None else auto_finish
        if should_finish:
            final_status = "failed" if event["level"] in {"error", "critical"} else "succeeded"
            finished = finish_run(
                rid,
                final_status,
                summary=message,
                scope=resolved_scope,
            )
            if finished.get("logging_persisted") is False:
                logging_warnings.append(str(finished.get("logging_warning") or "latest mirror failed"))
        else:
            run = log_repository.get_run(
                resolved_scope.database_file,
                project_id=resolved_scope.project_id,
                run_id=rid,
            )
            if run is not None:
                try:
                    _write_latest_mirror(run, resolved_scope)
                except Exception as exc:
                    logging_warnings.append(f"latest mirror: {type(exc).__name__}: {exc}")
        if logging_warnings:
            event["logging_persisted"] = False
            event["logging_warning"] = "; ".join(logging_warnings)
        return event
    except (EventRepositoryError, log_repository.LogRepositoryError) as exc:
        return execution_error(
            getattr(exc, "error_kind", "log_write_failed"),
            str(exc),
            tool=tool,
            run_id=rid,
        )


def import_legacy_jsonl(*, scope: LogScope | None = None) -> dict[str, Any]:
    resolved_scope = resolve_log_scope(scope=scope)
    return _prepare_scope(resolved_scope, force_import=True)


def esp_logs_latest() -> dict[str, Any]:
    scope = LogScope.active()
    try:
        snapshot = log_repository.read_latest_run_snapshot(
            scope.database_file,
            project_id=scope.project_id,
        )
    except FileNotFoundError:
        return {"ok": True, "latest": None}
    except log_repository.LogDatabaseQueryError as exc:
        return execution_error(
            exc.error_kind,
            str(exc),
            tool="esp_logs_latest",
            recoverable=exc.recoverable,
        )
    latest = snapshot["latest"]
    if latest is None:
        return {"ok": True, "latest": None}
    if snapshot["last_event"] is not None:
        latest["last_event"] = snapshot["last_event"]
    return {"ok": True, "latest": latest}


def esp_logs_get(run_id: str, tail: int = 80) -> dict[str, Any]:
    if tail < 1 or tail > 10_000:
        return execution_error("invalid_tail", "tail must be between 1 and 10000.", tool="esp_logs_get")
    scope = LogScope.active()
    try:
        snapshot = log_repository.read_run_snapshot(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=tail,
        )
    except FileNotFoundError:
        snapshot = {"run": None, "events": []}
    except log_repository.LogDatabaseQueryError as exc:
        return execution_error(
            exc.error_kind,
            str(exc),
            tool="esp_logs_get",
            recoverable=exc.recoverable,
        )
    run = snapshot["run"]
    if run is None:
        return {
            "ok": False,
            "error_kind": "run_not_found",
            "message": f"No log for run_id {run_id} in the active project",
        }
    events = snapshot["events"]
    return {"ok": True, "project_id": scope.project_id, "run_id": run_id, "run": run, "events": events}


def esp_logs_query(
    query: str = "",
    limit: int = 20,
    level: str | None = None,
    run_id: str | None = None,
    phase: str | None = None,
    tool: str | None = None,
    source: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    sequence_from: int | None = None,
    sequence_to: int | None = None,
) -> dict[str, Any]:
    if limit < 1 or limit > 1_000:
        return execution_error("invalid_limit", "limit must be between 1 and 1000.", tool="esp_logs_query")
    if sequence_from is not None and sequence_from < 1:
        return execution_error(
            "invalid_sequence_range",
            "sequence_from must be at least 1.",
            tool="esp_logs_query",
        )
    if sequence_to is not None and sequence_to < 1:
        return execution_error(
            "invalid_sequence_range",
            "sequence_to must be at least 1.",
            tool="esp_logs_query",
        )
    if sequence_from is not None and sequence_to is not None and sequence_from > sequence_to:
        return execution_error(
            "invalid_sequence_range",
            "sequence_from must not exceed sequence_to.",
            tool="esp_logs_query",
        )
    if (sequence_from is not None or sequence_to is not None) and not run_id:
        return execution_error(
            "run_id_required",
            "run_id is required when filtering by sequence number.",
            tool="esp_logs_query",
        )
    try:
        normalized_from_ts = normalize_timestamp(from_ts) if from_ts is not None else None
        normalized_to_ts = normalize_timestamp(to_ts) if to_ts is not None else None
    except EventRepositoryError as exc:
        return execution_error(exc.error_kind, str(exc), tool="esp_logs_query")
    if normalized_from_ts is not None and normalized_to_ts is not None and normalized_from_ts > normalized_to_ts:
        return execution_error(
            "invalid_time_range",
            "from_ts must not exceed to_ts.",
            tool="esp_logs_query",
        )
    try:
        terms = shlex.split(query)
    except ValueError:
        terms = query.split()
    if not terms and query.strip():
        terms = [query.strip()]
    scope = LogScope.active()
    try:
        snapshot = log_repository.query_events_readonly(
            scope.database_file,
            project_id=scope.project_id,
            terms=[term.lower() for term in terms],
            limit=limit,
            run_id=run_id,
            phase=phase,
            level=level,
            tool=tool,
            source=source,
            from_ts=normalized_from_ts,
            to_ts=normalized_to_ts,
            sequence_from=sequence_from,
            sequence_to=sequence_to,
        )
        matches = snapshot["matches"]
    except FileNotFoundError:
        matches = []
    except EventRepositoryError as exc:
        return execution_error(exc.error_kind, str(exc), tool="esp_logs_query")
    except log_repository.LogDatabaseQueryError as exc:
        return execution_error(
            exc.error_kind,
            str(exc),
            tool="esp_logs_query",
            recoverable=exc.recoverable,
        )
    return {
        "ok": True,
        "project_id": scope.project_id,
        "query": query,
        "terms": terms,
        "filters": {
            "run_id": run_id,
            "phase": phase,
            "level": level,
            "tool": tool,
            "source": source,
            "from_ts": normalized_from_ts,
            "to_ts": normalized_to_ts,
            "sequence_from": sequence_from,
            "sequence_to": sequence_to,
        },
        "matches": matches,
    }


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def logged_task(
    *,
    task_type: str | None = None,
    selected_port_arg: str | None = None,
    payload_args: tuple[str, ...] = (),
    result_payload_keys: tuple[str, ...] = (),
    completion_artifacts: tuple[str, ...] = (),
) -> Callable[[F], F]:
    if not isinstance(result_payload_keys, tuple) or not all(
        isinstance(key, str) and key for key in result_payload_keys
    ):
        raise TypeError("result_payload_keys must be a tuple of non-empty strings.")
    if (
        not isinstance(completion_artifacts, tuple)
        or not all(isinstance(policy, str) and policy for policy in completion_artifacts)
        or len(set(completion_artifacts)) != len(completion_artifacts)
    ):
        raise TypeError(
            "completion_artifacts must be a tuple of unique non-empty strings."
        )
    unsupported_policies = set(completion_artifacts) - _COMPLETION_ARTIFACT_POLICIES
    if unsupported_policies:
        raise ValueError(
            f"Unsupported completion artifact policies: {sorted(unsupported_policies)}"
        )
    completion_keys = _RESULT_LOG_KEYS | set(result_payload_keys)

    def decorator(func: F) -> F:
        signature = inspect.signature(func)

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            tool = func.__name__
            selected_port = (
                bound.arguments.get(selected_port_arg) if selected_port_arg is not None else None
            )
            port_parameter = (
                signature.parameters.get(selected_port_arg) if selected_port_arg is not None else None
            )
            if (
                selected_port_arg is not None
                and not selected_port
                and port_parameter is not None
                and port_parameter.default is None
            ):
                selected_port = get_selected_port()
                if selected_port:
                    bound.arguments[selected_port_arg] = selected_port
            if _TASK_RUN_CONTEXT.get() is not None:
                return func(*bound.args, **bound.kwargs)
            start_payload = {
                name: _json_safe(bound.arguments[name])
                for name in payload_args
                if name in bound.arguments
            }
            scope = LogScope.active()
            try:
                run = start_run(
                    task_type or tool,
                    selected_port=selected_port if isinstance(selected_port, str) else None,
                    payload=start_payload,
                    scope=scope,
                )
                run_id = run["run_id"]
                prepared = write_event(
                    tool,
                    "info",
                    f"{tool} started.",
                    start_payload,
                    run_id=run_id,
                    phase="prepare",
                    task_type=task_type or tool,
                    selected_port=selected_port if isinstance(selected_port, str) else None,
                    scope=scope,
                )
            except Exception as exc:
                if "run_id" in locals():
                    try:
                        finish_run(run_id, "failed", summary=str(exc), scope=scope)
                    except Exception:
                        pass
                return execution_error(
                    "log_start_failed",
                    f"Could not initialize the SQLite task log: {exc}",
                    tool=tool,
                    run_id=locals().get("run_id"),
                    project_id=scope.project_id,
                )
            if prepared.get("ok") is False:
                try:
                    finish_run(run_id, "failed", summary=prepared.get("message"), scope=scope)
                except Exception:
                    pass
                prepared.setdefault("run_id", run_id)
                prepared.setdefault("project_id", scope.project_id)
                return prepared

            prepare_warnings: list[str] = []
            if prepared.get("logging_persisted") is False:
                prepare_warnings.append(
                    f"prepare event: {prepared.get('logging_warning') or 'audit mirror failed'}"
                )
            token = _TASK_RUN_CONTEXT.set((run_id, scope))
            try:
                try:
                    result = func(*bound.args, **bound.kwargs)
                except Exception as exc:
                    try:
                        write_event(
                            tool,
                            "error",
                            f"{tool} raised {type(exc).__name__}: {exc}",
                            {"exception_type": type(exc).__name__},
                            run_id=run_id,
                            phase="complete",
                            scope=scope,
                        )
                        finish_run(run_id, "failed", summary=str(exc), scope=scope)
                    except Exception:
                        pass
                    raise

                ok = result.get("ok") is not False
                message = str(result.get("message") or f"{tool} {'completed' if ok else 'failed'}.")
                result_payload = {
                    key: _json_safe(result[key])
                    for key in completion_keys
                    if key in result
                }
                logging_warnings = list(prepare_warnings)
                completion_event_uuid = str(uuid4())
                completion_ts = now_iso()
                completion_bundle: log_repository.EventArtifacts | None = None
                artifacts_ready = True
                if completion_artifacts:
                    try:
                        completion_bundle = _build_completion_artifacts(
                            result=result,
                            event_uuid=completion_event_uuid,
                            scope=scope,
                            policies=completion_artifacts,
                        )
                    except Exception as exc:
                        artifacts_ready = False
                        logging_warnings.append(
                            "completion artifacts: "
                            f"{type(exc).__name__}: {exc}"
                        )
                try:
                    if artifacts_ready:
                        completed = write_event(
                            tool,
                            "info" if ok else "error",
                            message,
                            result_payload,
                            run_id=run_id,
                            ts=completion_ts,
                            phase="complete",
                            event_uuid=completion_event_uuid,
                            artifacts=completion_bundle,
                            scope=scope,
                        )
                        if completed.get("ok") is False:
                            logging_warnings.append(
                                "completion event: "
                                + str(
                                    completed.get("message")
                                    or completed.get("error_kind")
                                    or "event write failed"
                                )
                            )
                        elif completed.get("logging_persisted") is False:
                            logging_warnings.append(
                                f"completion event: {completed.get('logging_warning') or 'audit mirror failed'}"
                            )
                except Exception as exc:
                    logging_warnings.append(f"completion event: {type(exc).__name__}: {exc}")
                try:
                    finished = finish_run(
                        run_id,
                        "succeeded" if ok else "failed",
                        summary=message,
                        scope=scope,
                    )
                    if finished.get("logging_persisted") is False:
                        logging_warnings.append(
                            f"run finalization: {finished.get('logging_warning') or 'latest mirror failed'}"
                        )
                except Exception as exc:
                    logging_warnings.append(f"run finalization: {type(exc).__name__}: {exc}")

                result.setdefault("run_id", run_id)
                result.setdefault("project_id", scope.project_id)
                result["logging_persisted"] = not logging_warnings
                if logging_warnings:
                    result["logging_warning"] = "; ".join(logging_warnings)
                return result
            finally:
                _TASK_RUN_CONTEXT.reset(token)

        return wrapper  # type: ignore[return-value]

    return decorator
