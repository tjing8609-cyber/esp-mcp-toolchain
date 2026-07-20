from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import inspect
import json
from pathlib import Path
import shlex
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
}


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


def _event_is_mirrored(path: Path, event_uuid: str) -> bool:
    if not path.exists():
        return False
    return any(
        row.get("event_uuid") == event_uuid or row.get("event_id") == event_uuid
        for row in read_jsonl(path)
    )


def _mirror_event(event: dict[str, Any], scope: LogScope) -> None:
    path = session_path(event["run_id"], scope.log_root)
    if _event_is_mirrored(path, event["event_uuid"]):
        return
    mirror = dict(event)
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=event["run_id"],
    )
    if run is not None:
        mirror["task_type"] = run["task_type"]
        mirror["selected_port"] = run["selected_port"]
    append_jsonl(path, mirror)


def _write_latest_mirror(run: dict[str, Any], scope: LogScope) -> None:
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        tail=1,
    )
    last_event = events[-1] if events else None
    latest = {
        "project_id": scope.project_id,
        "run_id": run["run_id"],
        "task_type": run["task_type"],
        "status": run["status"],
        "last_tool": last_event["tool"] if last_event else run["task_type"],
        "has_error": run["status"] == "failed",
        "summary": run.get("summary") or (last_event["message"] if last_event else None),
        "updated_at": run.get("ended_at") or run["started_at"],
    }
    target = latest_path(scope.log_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        event, inserted = log_repository.append_event(
            resolved_scope.database_file,
            project_id=resolved_scope.project_id,
            run_id=rid,
            event_uuid=event_uuid,
            ts=ts or now_iso(),
            phase=phase,
            level=level,
            tool=tool,
            source=source,
            message=message,
            payload=payload,
        )
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
    _prepare_scope(scope)
    latest = log_repository.latest_run(scope.database_file, project_id=scope.project_id)
    if latest is None:
        return {"ok": True, "latest": None}
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=latest["run_id"],
        tail=1,
    )
    if events:
        latest["last_event"] = events[-1]
    return {"ok": True, "latest": latest}


def esp_logs_get(run_id: str, tail: int = 80) -> dict[str, Any]:
    scope = LogScope.active()
    _prepare_scope(scope)
    if tail < 1 or tail > 10_000:
        return execution_error("invalid_tail", "tail must be between 1 and 10000.", tool="esp_logs_get")
    run = log_repository.get_run(scope.database_file, project_id=scope.project_id, run_id=run_id)
    if run is None:
        return {
            "ok": False,
            "error_kind": "run_not_found",
            "message": f"No log for run_id {run_id} in the active project",
        }
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=tail,
    )
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
    scope = LogScope.active()
    _prepare_scope(scope)
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
    try:
        matches = log_repository.query_events(
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
    except EventRepositoryError as exc:
        return execution_error(exc.error_kind, str(exc), tool="esp_logs_query")
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
) -> Callable[[F], F]:
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
                    for key in _RESULT_LOG_KEYS
                    if key in result
                }
                logging_warnings = list(prepare_warnings)
                try:
                    completed = write_event(
                        tool,
                        "info" if ok else "error",
                        message,
                        result_payload,
                        run_id=run_id,
                        phase="complete",
                        scope=scope,
                    )
                    if completed.get("ok") is False:
                        logging_warnings.append(
                            str(completed.get("message") or completed.get("error_kind") or "event write failed")
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
