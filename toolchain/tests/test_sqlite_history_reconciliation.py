from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3
import threading
from uuid import uuid4

import pytest

from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.error_repository import (
    ErrorConflictError,
    stable_error_id,
)
from esp_mcp_toolchain.database.event_repository import InvalidEventError
from esp_mcp_toolchain.tools.log_tools import (
    LogScope,
    finish_run,
    start_run,
)


INPUT_TIMESTAMP = "2026-07-28T16:00:00+08:00"
TIMESTAMP = "2026-07-28T08:00:00+00:00"


def _raw_artifact(
    *,
    path: str = "serial/history/chunk-000001.bin",
) -> log_repository.RawLogArtifact:
    return log_repository.RawLogArtifact(
        kind="serial_monitor_chunk",
        path=path,
        sha256=hashlib.sha256(b"historical chunk").hexdigest(),
    )


def _error_artifact(
    event_uuid: str,
    *,
    message: str = "historical failure",
) -> log_repository.ErrorArtifact:
    return log_repository.ErrorArtifact(
        occurrence_key=f"event:{event_uuid}:historical_error",
        error_kind="micropython_traceback",
        file="main.py",
        line=23,
        exception_type="RuntimeError",
        message=message,
        raw_text=f"RuntimeError: {message}",
        recoverable=True,
    )


def _event_kwargs(scope: LogScope, run_id: str, event_uuid: str) -> dict:
    return {
        "database": scope.database_file,
        "project_id": scope.project_id,
        "run_id": run_id,
        "event_uuid": event_uuid,
        "ts": INPUT_TIMESTAMP,
        "phase": "complete",
        "level": "error",
        "tool": "history_contract",
        "source": "pytest",
        "message": "historical completion",
        "payload": {"historical": True},
    }


def _prepare_event(
    run_id: str,
    *,
    terminal: bool = True,
) -> tuple[LogScope, str, dict]:
    scope = LogScope.active()
    start_run(
        "history_contract",
        run_id=run_id,
        summary="original summary",
        payload={"original": True},
        scope=scope,
    )
    event_uuid = str(uuid4())
    event, inserted = log_repository.append_event(
        **_event_kwargs(scope, run_id, event_uuid)
    )
    assert inserted is True
    if terminal:
        finish_run(
            run_id,
            "failed",
            summary="finished summary",
            payload={"finished": True},
            scope=scope,
        )
    return scope, event_uuid, event


def _artifacts(event_uuid: str) -> log_repository.EventArtifacts:
    return log_repository.EventArtifacts(
        raw_logs=(_raw_artifact(),),
        errors=(_error_artifact(event_uuid),),
    )


def _reconcile(
    scope: LogScope,
    run_id: str,
    event_uuid: str | None,
    *,
    artifacts: log_repository.EventArtifacts,
) -> dict:
    return log_repository.reconcile_existing_event_artifacts(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        event_uuid=event_uuid,
        artifacts=artifacts,
    )


def _snapshot(
    scope: LogScope,
    run_id: str,
    *,
    project_id: str | None = None,
) -> dict:
    selected_project = project_id or scope.project_id
    return {
        "run": log_repository.get_run(
            scope.database_file,
            project_id=selected_project,
            run_id=run_id,
        ),
        "events": log_repository.get_run_events(
            scope.database_file,
            project_id=selected_project,
            run_id=run_id,
            tail=20,
        ),
        "raw_logs": log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=selected_project,
            run_id=run_id,
        ),
        "errors": log_repository.get_run_errors(
            scope.database_file,
            project_id=selected_project,
            run_id=run_id,
        ),
    }


def test_reconcile_existing_terminal_event_adds_artifacts_without_mutating_run_or_event():
    run_id = "history-existing-event"
    scope, event_uuid, event = _prepare_event(run_id)
    before = _snapshot(scope, run_id)

    report = _reconcile(
        scope,
        run_id,
        event_uuid,
        artifacts=_artifacts(event_uuid),
    )

    after = _snapshot(scope, run_id)
    assert report["event"] == event
    assert report["event_inserted"] is False
    assert [entry["inserted"] for entry in report["raw_logs"]] == [True]
    assert [entry["inserted"] for entry in report["errors"]] == [True]
    assert report["raw_logs"][0]["record"]["created_at"] == TIMESTAMP
    assert report["errors"][0]["record"]["created_at"] == TIMESTAMP
    assert after["run"] == before["run"]
    assert after["events"] == before["events"]
    assert len(after["raw_logs"]) == 1
    assert len(after["errors"]) == 1


def test_reconcile_requires_last_complete_event_on_terminal_run():
    prepare_run = "history-terminal-prepare-event"
    prepare_scope = LogScope.active()
    start_run(
        "history_contract",
        run_id=prepare_run,
        summary="prepare-only run",
        scope=prepare_scope,
    )
    prepare_event_uuid = str(uuid4())
    prepare_kwargs = _event_kwargs(
        prepare_scope,
        prepare_run,
        prepare_event_uuid,
    )
    prepare_kwargs["phase"] = "prepare"
    log_repository.append_event(**prepare_kwargs)
    finish_run(
        prepare_run,
        "failed",
        summary="terminal without complete event",
        scope=prepare_scope,
    )
    prepare_before = _snapshot(prepare_scope, prepare_run)

    with pytest.raises(log_repository.EventNotTerminalError) as exc_info:
        _reconcile(
            prepare_scope,
            prepare_run,
            prepare_event_uuid,
            artifacts=_artifacts(prepare_event_uuid),
        )

    assert exc_info.value.error_kind == "event_not_terminal"
    assert _snapshot(prepare_scope, prepare_run) == prepare_before

    later_run = "history-complete-not-last"
    later_scope, complete_event_uuid, _event = _prepare_event(
        later_run,
        terminal=False,
    )
    later_event_uuid = str(uuid4())
    later_kwargs = _event_kwargs(later_scope, later_run, later_event_uuid)
    later_kwargs["phase"] = "execute"
    log_repository.append_event(**later_kwargs)
    finish_run(
        later_run,
        "failed",
        summary="later event follows complete event",
        scope=later_scope,
    )
    later_before = _snapshot(later_scope, later_run)

    with pytest.raises(log_repository.EventNotTerminalError) as exc_info:
        _reconcile(
            later_scope,
            later_run,
            complete_event_uuid,
            artifacts=_artifacts(complete_event_uuid),
        )

    assert exc_info.value.error_kind == "event_not_terminal"
    assert _snapshot(later_scope, later_run) == later_before


def test_reconcile_requires_explicit_event_uuid_and_never_generates_one():
    run_id = "history-explicit-event"
    scope, event_uuid, _event = _prepare_event(run_id)
    before = _snapshot(scope, run_id)

    with pytest.raises(log_repository.LogRepositoryError, match="event_uuid"):
        _reconcile(
            scope,
            run_id,
            None,
            artifacts=_artifacts(event_uuid),
        )

    assert _snapshot(scope, run_id) == before


def test_reconcile_refuses_missing_run_event_and_cross_project_binding():
    seed_run = "history-seed-run"
    scope, _seed_event_uuid, _seed_event = _prepare_event(seed_run)
    seed_before = _snapshot(scope, seed_run)
    missing_run = "history-missing-run"
    missing_event_uuid = str(uuid4())

    with pytest.raises(log_repository.RunNotFoundError):
        _reconcile(
            scope,
            missing_run,
            missing_event_uuid,
            artifacts=log_repository.EMPTY_EVENT_ARTIFACTS,
        )
    assert _snapshot(scope, missing_run) == {
        "run": None,
        "events": [],
        "raw_logs": [],
        "errors": [],
    }
    assert _snapshot(scope, seed_run) == seed_before

    run_id = "history-binding-run"
    scope, event_uuid, _event = _prepare_event(run_id)
    before = _snapshot(scope, run_id)
    with pytest.raises(log_repository.EventNotFoundError):
        _reconcile(
            scope,
            run_id,
            str(uuid4()),
            artifacts=_artifacts(event_uuid),
        )

    other_run_same_project = "history-other-run"
    _scope, other_run_event_uuid, _other_event = _prepare_event(
        other_run_same_project
    )
    other_run_before = _snapshot(scope, other_run_same_project)
    with pytest.raises(log_repository.EventNotFoundError):
        _reconcile(
            scope,
            run_id,
            other_run_event_uuid,
            artifacts=_artifacts(event_uuid),
        )

    other_project = "other-project"
    other_run = run_id
    other_event_uuid = str(uuid4())
    log_repository.create_run(
        scope.database_file,
        project_id=other_project,
        run_id=other_run,
        task_type="history_contract",
        started_at=INPUT_TIMESTAMP,
    )
    log_repository.append_event(
        database=scope.database_file,
        project_id=other_project,
        run_id=other_run,
        event_uuid=other_event_uuid,
        ts=INPUT_TIMESTAMP,
        phase="complete",
        level="info",
        tool="history_contract",
        source="pytest",
        message="other project",
        payload={},
    )
    log_repository.finish_run(
        scope.database_file,
        project_id=other_project,
        run_id=other_run,
        status="succeeded",
        ended_at=INPUT_TIMESTAMP,
    )
    other_project_before = _snapshot(
        scope,
        other_run,
        project_id=other_project,
    )
    with pytest.raises(log_repository.EventNotFoundError):
        _reconcile(
            scope,
            run_id,
            other_event_uuid,
            artifacts=_artifacts(event_uuid),
        )

    assert _snapshot(scope, run_id) == before
    assert _snapshot(scope, other_run_same_project) == other_run_before
    assert _snapshot(
        scope,
        other_run,
        project_id=other_project,
    ) == other_project_before


def test_reconcile_rejects_running_run_without_changing_sequence():
    run_id = "history-running-run"
    scope, event_uuid, _event = _prepare_event(run_id, terminal=False)
    before = _snapshot(scope, run_id)

    with pytest.raises(log_repository.EventNotFoundError):
        _reconcile(
            scope,
            run_id,
            str(uuid4()),
            artifacts=_artifacts(event_uuid),
        )

    foreign_run = "history-running-foreign-event"
    _scope, foreign_event_uuid, _foreign_event = _prepare_event(foreign_run)
    foreign_before = _snapshot(scope, foreign_run)

    with pytest.raises(log_repository.EventNotFoundError):
        _reconcile(
            scope,
            run_id,
            foreign_event_uuid,
            artifacts=_artifacts(event_uuid),
        )

    other_project = "history-running-other-project"
    other_project_event_uuid = str(uuid4())
    log_repository.create_run(
        scope.database_file,
        project_id=other_project,
        run_id=run_id,
        task_type="history_contract",
        started_at=INPUT_TIMESTAMP,
    )
    log_repository.append_event(
        database=scope.database_file,
        project_id=other_project,
        run_id=run_id,
        event_uuid=other_project_event_uuid,
        ts=INPUT_TIMESTAMP,
        phase="complete",
        level="info",
        tool="history_contract",
        source="pytest",
        message="same run id in another project",
        payload={},
    )
    log_repository.finish_run(
        scope.database_file,
        project_id=other_project,
        run_id=run_id,
        status="succeeded",
        ended_at=INPUT_TIMESTAMP,
    )
    other_project_before = _snapshot(
        scope,
        run_id,
        project_id=other_project,
    )

    with pytest.raises(log_repository.EventNotFoundError):
        _reconcile(
            scope,
            run_id,
            other_project_event_uuid,
            artifacts=_artifacts(event_uuid),
        )

    with pytest.raises(log_repository.RunNotTerminalError):
        _reconcile(
            scope,
            run_id,
            event_uuid,
            artifacts=_artifacts(event_uuid),
        )

    assert _snapshot(scope, run_id) == before
    assert _snapshot(scope, foreign_run) == foreign_before
    assert _snapshot(
        scope,
        run_id,
        project_id=other_project,
    ) == other_project_before


def test_reconcile_exact_retry_is_strictly_idempotent():
    run_id = "history-idempotent-run"
    scope, event_uuid, _event = _prepare_event(run_id)
    before = _snapshot(scope, run_id)
    artifacts = _artifacts(event_uuid)

    first = _reconcile(scope, run_id, event_uuid, artifacts=artifacts)
    retry = _reconcile(scope, run_id, event_uuid, artifacts=artifacts)

    after = _snapshot(scope, run_id)
    assert [entry["inserted"] for entry in first["raw_logs"]] == [True]
    assert [entry["inserted"] for entry in first["errors"]] == [True]
    assert [entry["inserted"] for entry in retry["raw_logs"]] == [False]
    assert [entry["inserted"] for entry in retry["errors"]] == [False]
    assert after["run"] == before["run"]
    assert after["events"] == before["events"]
    assert len(after["raw_logs"]) == 1
    assert len(after["errors"]) == 1


def test_reconcile_late_error_conflict_rolls_back_new_raw(monkeypatch):
    run_id = "history-conflict-run"
    scope, event_uuid, _event = _prepare_event(run_id)
    error = _error_artifact(event_uuid)
    error_id = stable_error_id(
        project_id=scope.project_id,
        run_id=run_id,
        occurrence_key=error.occurrence_key,
        error_kind=error.error_kind,
        file=error.file,
        line=error.line,
        column=error.column,
        exception_type=error.exception_type,
        message=error.message,
        raw_text=error.raw_text,
    )
    log_repository.register_error(
        scope.database_file,
        project_id=scope.project_id,
        error_id=error_id,
        run_id=run_id,
        error_kind=error.error_kind,
        file=error.file,
        line=error.line,
        column=error.column,
        exception_type=error.exception_type,
        message="pre-existing conflicting content",
        raw_text=error.raw_text,
        recoverable=error.recoverable,
        created_at=TIMESTAMP,
    )
    before = _snapshot(scope, run_id)
    raw_inserted: list[bool] = []
    original_insert_raw_log = log_repository.insert_raw_log

    def record_raw_insert(*args, **kwargs):
        record, inserted = original_insert_raw_log(*args, **kwargs)
        raw_inserted.append(inserted)
        return record, inserted

    monkeypatch.setattr(
        log_repository,
        "insert_raw_log",
        record_raw_insert,
    )

    with pytest.raises(log_repository.ArtifactProjectionError) as exc_info:
        _reconcile(
            scope,
            run_id,
            event_uuid,
            artifacts=log_repository.EventArtifacts(
                raw_logs=(_raw_artifact(),),
                errors=(error,),
            ),
        )

    assert isinstance(exc_info.value.__cause__, ErrorConflictError)
    assert raw_inserted == [True]
    assert _snapshot(scope, run_id) == before


def test_reconcile_late_sqlite_failure_rolls_back_entire_bundle(monkeypatch):
    run_id = "history-sqlite-failure"
    scope, event_uuid, _event = _prepare_event(run_id)
    before = _snapshot(scope, run_id)
    raw_inserted: list[bool] = []
    original_insert_raw_log = log_repository.insert_raw_log

    def record_raw_insert(*args, **kwargs):
        record, inserted = original_insert_raw_log(*args, **kwargs)
        raw_inserted.append(inserted)
        return record, inserted

    def fail_error_insert(*_args, **_kwargs):
        raise sqlite3.OperationalError("forced historical artifact failure")

    monkeypatch.setattr(
        log_repository,
        "insert_raw_log",
        record_raw_insert,
    )
    monkeypatch.setattr(log_repository, "insert_error", fail_error_insert)
    with pytest.raises(log_repository.ArtifactProjectionError) as exc_info:
        _reconcile(
            scope,
            run_id,
            event_uuid,
            artifacts=_artifacts(event_uuid),
        )

    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert raw_inserted == [True]
    assert _snapshot(scope, run_id) == before


def test_reconcile_invalid_error_timestamp_is_wrapped_and_rolls_back_raw(
    monkeypatch,
):
    run_id = "history-invalid-error-timestamp"
    scope, event_uuid, _event = _prepare_event(run_id)
    before = _snapshot(scope, run_id)
    base_error = _error_artifact(event_uuid)
    invalid_error = log_repository.ErrorArtifact(
        occurrence_key=base_error.occurrence_key,
        error_kind=base_error.error_kind,
        file=base_error.file,
        line=base_error.line,
        column=base_error.column,
        exception_type=base_error.exception_type,
        message=base_error.message,
        raw_text=base_error.raw_text,
        recoverable=base_error.recoverable,
        created_at="not-a-timestamp",
    )
    raw_inserted: list[bool] = []
    original_insert_raw_log = log_repository.insert_raw_log

    def record_raw_insert(*args, **kwargs):
        record, inserted = original_insert_raw_log(*args, **kwargs)
        raw_inserted.append(inserted)
        return record, inserted

    monkeypatch.setattr(
        log_repository,
        "insert_raw_log",
        record_raw_insert,
    )

    with pytest.raises(log_repository.ArtifactProjectionError) as exc_info:
        _reconcile(
            scope,
            run_id,
            event_uuid,
            artifacts=log_repository.EventArtifacts(
                raw_logs=(_raw_artifact(),),
                errors=(invalid_error,),
            ),
        )

    assert isinstance(exc_info.value.__cause__, InvalidEventError)
    assert raw_inserted == [True]
    assert _snapshot(scope, run_id) == before


def test_reconcile_commit_failure_is_wrapped_and_rolls_back_bundle(
    monkeypatch,
):
    run_id = "history-commit-failure"
    scope, event_uuid, _event = _prepare_event(run_id)
    before = _snapshot(scope, run_id)
    raw_inserted: list[bool] = []
    original_insert_raw_log = log_repository.insert_raw_log
    original_connect = log_repository.connect

    def record_raw_insert(*args, **kwargs):
        record, inserted = original_insert_raw_log(*args, **kwargs)
        raw_inserted.append(inserted)
        return record, inserted

    class CommitFailConnection:
        def __init__(self, connection):
            self._connection = connection

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def commit(self):
            raise sqlite3.OperationalError("forced reconciliation commit failure")

    def fail_commit_connect(database):
        return CommitFailConnection(original_connect(database))

    monkeypatch.setattr(
        log_repository,
        "insert_raw_log",
        record_raw_insert,
    )
    monkeypatch.setattr(log_repository, "connect", fail_commit_connect)

    with pytest.raises(log_repository.ArtifactProjectionError) as exc_info:
        _reconcile(
            scope,
            run_id,
            event_uuid,
            artifacts=_artifacts(event_uuid),
        )

    monkeypatch.setattr(log_repository, "connect", original_connect)
    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert raw_inserted == [True]
    assert _snapshot(scope, run_id) == before


def test_concurrent_reconcile_same_bundle_commits_once():
    run_id = "history-concurrent-run"
    scope, event_uuid, _event = _prepare_event(run_id)
    before = _snapshot(scope, run_id)
    artifacts = _artifacts(event_uuid)
    barrier = threading.Barrier(2)

    def reconcile_once() -> dict:
        barrier.wait(timeout=5)
        return _reconcile(
            scope,
            run_id,
            event_uuid,
            artifacts=artifacts,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(executor.map(lambda _index: reconcile_once(), range(2)))

    after = _snapshot(scope, run_id)
    assert sum(
        int(report["raw_logs"][0]["inserted"]) for report in reports
    ) == 1
    assert sum(
        int(report["errors"][0]["inserted"]) for report in reports
    ) == 1
    assert after["run"] == before["run"]
    assert after["events"] == before["events"]
    assert len(after["raw_logs"]) == 1
    assert len(after["errors"]) == 1
