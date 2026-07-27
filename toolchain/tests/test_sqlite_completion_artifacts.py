from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.error_repository import (
    ErrorConflictError,
    stable_error_id,
)
from esp_mcp_toolchain.database.raw_log_repository import (
    RawLogConflictError,
    stable_raw_log_id,
)
from esp_mcp_toolchain.tools import exec_tools, log_tools, serial_tools
from esp_mcp_toolchain.tools.log_tools import (
    LogScope,
    finish_run,
    logged_task,
    start_run,
)


INPUT_TIMESTAMP = "2026-07-28T08:30:00+08:00"
TIMESTAMP = "2026-07-28T00:30:00+00:00"


def _raw_artifact(*, path: str = "raw/capture.log") -> log_repository.RawLogArtifact:
    return log_repository.RawLogArtifact(
        kind="serial_capture_raw",
        path=path,
        sha256=hashlib.sha256(b"capture").hexdigest(),
    )


def _error_artifact(
    event_uuid: str,
    *,
    message: str = "buzzer failed",
) -> log_repository.ErrorArtifact:
    return log_repository.ErrorArtifact(
        occurrence_key=f"event:{event_uuid}:structured_error",
        error_kind="micropython_traceback",
        file="main.py",
        line=12,
        column=None,
        exception_type="RuntimeError",
        message=message,
        raw_text="RuntimeError: buzzer failed",
        recoverable=True,
    )


def _append_kwargs(scope: LogScope, run_id: str, event_uuid: str) -> dict:
    return {
        "database": scope.database_file,
        "project_id": scope.project_id,
        "run_id": run_id,
        "event_uuid": event_uuid,
        "ts": INPUT_TIMESTAMP,
        "phase": "complete",
        "level": "error",
        "tool": "artifact_contract",
        "source": "pytest",
        "message": "completion",
        "payload": {"has_error": True},
    }


def test_event_raw_and_error_commit_atomically_and_retry_idempotently():
    scope = LogScope.active()
    run = start_run("artifact_contract", run_id="artifact-atomic-run", scope=scope)
    event_uuid = str(uuid4())
    raw_artifact = _raw_artifact()
    error_artifact = _error_artifact(event_uuid)
    artifacts = log_repository.EventArtifacts(
        raw_logs=(raw_artifact,),
        errors=(error_artifact,),
    )

    first_report = log_repository.append_event_with_artifacts(
        **_append_kwargs(scope, run["run_id"], event_uuid),
        artifacts=artifacts,
    )
    retry_report = log_repository.append_event_with_artifacts(
        **_append_kwargs(scope, run["run_id"], event_uuid),
        artifacts=artifacts,
    )

    assert first_report["event_inserted"] is True
    assert retry_report["event_inserted"] is False
    assert retry_report["event"]["event_uuid"] == first_report["event"]["event_uuid"]
    assert [entry["inserted"] for entry in first_report["raw_logs"]] == [True]
    assert [entry["inserted"] for entry in first_report["errors"]] == [True]
    assert [entry["inserted"] for entry in retry_report["raw_logs"]] == [False]
    assert [entry["inserted"] for entry in retry_report["errors"]] == [False]
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    ) == [first_report["raw_logs"][0]["record"]]
    assert log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    ) == [first_report["errors"][0]["record"]]
    assert first_report["raw_logs"][0]["record"]["created_at"] == TIMESTAMP
    assert first_report["errors"][0]["record"]["created_at"] == TIMESTAMP
    assert first_report["event"]["ts"] == TIMESTAMP
    assert first_report["raw_logs"][0]["record"]["raw_log_id"] == stable_raw_log_id(
        project_id=scope.project_id,
        run_id=run["run_id"],
        kind=raw_artifact.kind,
        path=raw_artifact.path,
    )


def test_append_event_keeps_legacy_two_tuple_contract():
    scope = LogScope.active()
    run = start_run("legacy_append", run_id="legacy-append-run", scope=scope)

    event, inserted = log_repository.append_event(
        **_append_kwargs(scope, run["run_id"], str(uuid4()))
    )
    retry, retry_inserted = log_repository.append_event(
        **_append_kwargs(scope, run["run_id"], event["event_uuid"])
    )

    assert inserted is True
    assert retry_inserted is False
    assert retry["event_uuid"] == event["event_uuid"]
    assert event["sequence_no"] == 1
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    )["next_sequence_no"] == 2


def test_artifact_conflict_rolls_back_event_raw_and_sequence_number():
    scope = LogScope.active()
    run = start_run("artifact_rollback", run_id="artifact-rollback-run", scope=scope)
    event_uuid = str(uuid4())
    raw_artifact = _raw_artifact()
    conflicting_error = _error_artifact(event_uuid, message="conflicting")
    conflicting_error_id = stable_error_id(
        project_id=scope.project_id,
        run_id=run["run_id"],
        occurrence_key=conflicting_error.occurrence_key,
        error_kind=conflicting_error.error_kind,
        file=conflicting_error.file,
        line=conflicting_error.line,
        column=conflicting_error.column,
        exception_type=conflicting_error.exception_type,
        message=conflicting_error.message,
        raw_text=conflicting_error.raw_text,
    )
    log_repository.register_error(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        error_id=conflicting_error_id,
        error_kind=conflicting_error.error_kind,
        file=conflicting_error.file,
        line=conflicting_error.line,
        column=conflicting_error.column,
        exception_type=conflicting_error.exception_type,
        message="existing",
        raw_text=conflicting_error.raw_text,
        recoverable=conflicting_error.recoverable,
        created_at=TIMESTAMP,
    )

    with pytest.raises(log_repository.ArtifactProjectionError) as exc_info:
        log_repository.append_event_with_artifacts(
            **_append_kwargs(scope, run["run_id"], event_uuid),
            artifacts=log_repository.EventArtifacts(
                raw_logs=(raw_artifact,),
                errors=(conflicting_error,),
            ),
        )

    assert isinstance(exc_info.value.__cause__, ErrorConflictError)
    assert log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        tail=10,
    ) == []
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    ) == []
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    )["next_sequence_no"] == 1
    assert log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    )[0]["message"] == "existing"


def test_unexpected_sqlite_artifact_error_is_wrapped_and_rolled_back(monkeypatch):
    scope = LogScope.active()
    run = start_run("artifact_sqlite_failure", run_id="artifact-sqlite-run", scope=scope)

    def fail_insert(*_args, **_kwargs):
        raise sqlite3.OperationalError("forced SQLite write failure")

    monkeypatch.setattr(log_repository, "insert_raw_log", fail_insert)

    with pytest.raises(log_repository.ArtifactProjectionError) as exc_info:
        log_repository.append_event_with_artifacts(
            **_append_kwargs(scope, run["run_id"], str(uuid4())),
            artifacts=log_repository.EventArtifacts(raw_logs=(_raw_artifact(),)),
        )

    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        tail=10,
    ) == []
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    )["next_sequence_no"] == 1


def test_existing_terminal_event_retry_can_reconcile_missing_artifacts():
    scope = LogScope.active()
    run = start_run("artifact_reconcile", run_id="artifact-reconcile-run", scope=scope)
    event_uuid = str(uuid4())
    original, inserted = log_repository.append_event(
        **_append_kwargs(scope, run["run_id"], event_uuid)
    )
    finish_run(run["run_id"], "failed", scope=scope)
    raw_artifact = _raw_artifact()
    error_artifact = _error_artifact(event_uuid)

    report = log_repository.append_event_with_artifacts(
        **_append_kwargs(scope, run["run_id"], event_uuid),
        artifacts=log_repository.EventArtifacts(
            raw_logs=(raw_artifact,),
            errors=(error_artifact,),
        ),
    )

    assert inserted is True
    assert report["event_inserted"] is False
    assert report["event"]["event_uuid"] == original["event_uuid"]
    assert [entry["inserted"] for entry in report["raw_logs"]] == [True]
    assert [entry["inserted"] for entry in report["errors"]] == [True]
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    )["next_sequence_no"] == 2


class CaptureSerial:
    chunks: list[bytes] = []

    def __init__(self):
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.write_timeout = None
        self.rtscts = True
        self.dsrdtr = True
        self.xonxoff = True
        self.dtr = True
        self.rts = True
        self._chunks = list(type(self).chunks)

    def open(self) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def close(self) -> None:
        return None


class CaptureSerialModule:
    Serial = CaptureSerial


def test_capture_completion_registers_relative_raw_and_occurrence_error(monkeypatch):
    raw_bytes = (
        b"Traceback (most recent call last):\r\n"
        b'  File "main.py", line 12\r\n'
        b"RuntimeError: buzzer failed\r\n"
    )
    CaptureSerial.chunks = [raw_bytes]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=True,
        session_name="sqlite-artifact",
    )
    scope = LogScope.active()
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
        tail=10,
    )
    completion = next(event for event in events if event["phase"] == "complete")
    raw_logs = log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    )
    errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    )

    assert result["ok"] is True
    assert result["has_error"] is True
    assert len(raw_logs) == 1
    assert raw_logs[0]["kind"] == "serial_capture_raw"
    assert raw_logs[0]["path"].startswith("raw/")
    assert "\\" not in raw_logs[0]["path"]
    assert ":" not in raw_logs[0]["path"]
    assert raw_logs[0]["sha256"] == hashlib.sha256(raw_bytes).hexdigest()
    assert raw_logs[0]["created_at"] == completion["ts"]
    assert len(errors) == 1
    assert errors[0]["exception_type"] == "RuntimeError"
    assert errors[0]["created_at"] == completion["ts"]
    assert errors[0]["error_id"] == stable_error_id(
        project_id=scope.project_id,
        run_id=result["run_id"],
        occurrence_key=f"event:{completion['event_uuid']}:structured_error",
        error_kind=errors[0]["error_kind"],
        file=errors[0]["file"],
        line=errors[0]["line"],
        column=errors[0]["column"],
        exception_type=errors[0]["exception_type"],
        message=errors[0]["message"],
        raw_text=errors[0]["raw_text"],
    )
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    )["status"] == "succeeded"


def test_capture_without_traceback_registers_raw_but_no_error(monkeypatch):
    raw_bytes = b"boot complete\r\n"
    CaptureSerial.chunks = [raw_bytes]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=False,
        session_name="sqlite-no-error",
    )
    scope = LogScope.active()

    assert len(
        log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=result["run_id"],
        )
    ) == 1
    assert log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    ) == []


def test_program_stop_failure_registers_error_but_expected_success_does_not(monkeypatch):
    monkeypatch.setattr(
        exec_tools,
        "interrupt_program",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error_kind": "program_stop_unconfirmed",
            "recoverable": True,
            "message": "Prompt was not observed.",
            "observed_keyboard_interrupt": True,
            "observed_prompt": False,
            "stop_confirmed": False,
            "raw_path": "C:\\untrusted\\fake.log",
            "bytes_read": 99,
            "has_error": True,
            "error_report": {
                "has_error": True,
                "error_kind": "micropython_traceback",
                "exception_type": "KeyboardInterrupt",
            },
        },
    )
    failed = exec_tools.esp_program_stop(port="COM_STOP", timeout_ms=100)
    scope = LogScope.active()
    failed_errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=failed["run_id"],
    )

    monkeypatch.setattr(
        exec_tools,
        "interrupt_program",
        lambda *_args, **_kwargs: {
            "ok": True,
            "message": "Program stopped.",
            "interrupt_sent": True,
            "stop_confirmed": True,
            "observed_keyboard_interrupt": True,
            "observed_prompt": True,
            "raw_path": "C:\\untrusted\\fake.log",
            "bytes_read": 99,
            "has_error": True,
            "error_report": {
                "has_error": True,
                "error_kind": "micropython_traceback",
                "exception_type": "KeyboardInterrupt",
            },
        },
    )
    succeeded = exec_tools.esp_program_stop(port="COM_STOP", timeout_ms=100)
    succeeded_errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=succeeded["run_id"],
    )

    assert len(failed_errors) == 1
    assert failed_errors[0]["error_kind"] == "program_stop_unconfirmed"
    assert failed_errors[0]["message"] == "Prompt was not observed."
    assert succeeded_errors == []
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=failed["run_id"],
    ) == []
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=succeeded["run_id"],
    ) == []


def test_business_success_survives_atomic_artifact_logging_failure(monkeypatch):
    CaptureSerial.chunks = [b"captured"]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)
    original = log_repository.append_event_with_artifacts

    def fail_only_artifact_event(*args, artifacts=log_repository.EventArtifacts(), **kwargs):
        if artifacts.raw_logs or artifacts.errors:
            raise RawLogConflictError("forced artifact conflict")
        return original(
            *args,
            artifacts=artifacts,
            **kwargs,
        )

    monkeypatch.setattr(
        log_repository,
        "append_event_with_artifacts",
        fail_only_artifact_event,
    )

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=False,
        session_name="logging-gap",
    )
    scope = LogScope.active()
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    )
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
        tail=10,
    )

    assert result["ok"] is True
    assert result["logging_persisted"] is False
    assert "forced artifact conflict" in result["logging_warning"]
    assert Path(result["raw_path"]).read_bytes() == b"captured"
    assert run["status"] == "succeeded"
    assert [event["phase"] for event in events] == ["prepare"]


def test_business_failure_survives_atomic_artifact_logging_failure(monkeypatch):
    monkeypatch.setattr(
        exec_tools,
        "interrupt_program",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error_kind": "program_stop_unconfirmed",
            "recoverable": True,
            "message": "Original stop failure.",
        },
    )

    original = log_repository.append_event_with_artifacts

    def fail_artifact_event(
        *args,
        artifacts=log_repository.EventArtifacts(),
        **kwargs,
    ):
        if artifacts.raw_logs or artifacts.errors:
            raise log_repository.ArtifactProjectionError("forced artifact failure")
        return original(*args, artifacts=artifacts, **kwargs)

    monkeypatch.setattr(
        log_repository,
        "append_event_with_artifacts",
        fail_artifact_event,
    )

    result = exec_tools.esp_program_stop(port="COM_STOP", timeout_ms=100)
    scope = LogScope.active()
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    )
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
        tail=10,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "program_stop_unconfirmed"
    assert result["message"] == "Original stop failure."
    assert result["logging_persisted"] is False
    assert "forced artifact failure" in result["logging_warning"]
    assert run["status"] == "failed"
    assert [event["phase"] for event in events] == ["prepare"]


@pytest.mark.parametrize("failure_kind", ["outside_raw_root", "size_mismatch"])
def test_untrusted_capture_raw_is_not_projected_and_completion_is_not_downgraded(
    isolated_project_context,
    failure_kind,
):
    scope = LogScope.active()
    if failure_kind == "outside_raw_root":
        raw_path = isolated_project_context / "outside.log"
        raw_path.write_bytes(b"capture")
        bytes_read = len(b"capture")
    else:
        raw_root = scope.log_root / "raw"
        raw_root.mkdir(parents=True, exist_ok=True)
        raw_path = raw_root / "wrong-size.log"
        raw_path.write_bytes(b"capture")
        bytes_read = len(b"capture") + 1

    @logged_task(
        task_type="untrusted_capture",
        completion_artifacts=("serial_capture_raw",),
    )
    def untrusted_capture() -> dict:
        return {
            "ok": True,
            "raw_path": str(raw_path),
            "bytes_read": bytes_read,
            "message": "Business action completed.",
        }

    result = untrusted_capture()
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
        tail=10,
    )

    assert result["ok"] is True
    assert result["logging_persisted"] is False
    assert "completion artifacts" in result["logging_warning"]
    assert [event["phase"] for event in events] == ["prepare"]
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    ) == []


def test_reparse_capture_raw_is_rejected_without_completion_fallback(monkeypatch):
    scope = LogScope.active()
    raw_root = scope.log_root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    raw_path = raw_root / "reparse.log"
    raw_path.write_bytes(b"capture")
    original_is_reparse = log_tools._is_reparse_point
    monkeypatch.setattr(
        log_tools,
        "_is_reparse_point",
        lambda path: path == raw_path or original_is_reparse(path),
    )

    @logged_task(
        task_type="reparse_capture",
        completion_artifacts=("serial_capture_raw",),
    )
    def reparse_capture() -> dict:
        return {
            "ok": True,
            "raw_path": str(raw_path),
            "bytes_read": len(b"capture"),
            "message": "Business action completed.",
        }

    result = reparse_capture()
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
        tail=10,
    )

    assert result["ok"] is True
    assert result["logging_persisted"] is False
    assert "reparse point" in result["logging_warning"]
    assert [event["phase"] for event in events] == ["prepare"]
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    ) == []


def test_recovery_path_is_never_registered_as_final_raw(monkeypatch):
    CaptureSerial.chunks = [b"partial capture"]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)
    scope = LogScope.active()
    recovery_path = scope.log_root / "raw" / "partial.log"
    recovery_path.parent.mkdir(parents=True, exist_ok=True)
    recovery_path.write_bytes(b"partial capture")

    def fail_persistence(*_args, **_kwargs):
        raise serial_tools.SerialCapturePersistError(
            recovery_path,
            "fsync",
            OSError("forced fsync failure"),
        )

    monkeypatch.setattr(serial_tools, "_write_raw_capture", fail_persistence)

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=False,
        session_name="recovery-not-raw",
    )

    assert result["ok"] is False
    assert result["recovery_path"] == str(recovery_path)
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    ) == []
    errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    )
    completion = next(
        event
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=result["run_id"],
            tail=10,
        )
        if event["phase"] == "complete"
    )
    assert [entry["error_kind"] for entry in errors] == [
        "serial_capture_persist_failed"
    ]
    assert completion["payload_json"]["recovery_path"] == str(recovery_path)
    assert errors[0]["created_at"] == completion["ts"]
    assert errors[0]["error_id"] == stable_error_id(
        project_id=scope.project_id,
        run_id=result["run_id"],
        occurrence_key=f"event:{completion['event_uuid']}:result_error",
        error_kind=errors[0]["error_kind"],
        file=errors[0]["file"],
        line=errors[0]["line"],
        column=errors[0]["column"],
        exception_type=errors[0]["exception_type"],
        message=errors[0]["message"],
        raw_text=errors[0]["raw_text"],
    )


def test_capture_persistence_failure_with_traceback_registers_two_distinct_errors(
    monkeypatch,
):
    raw_bytes = (
        b"Traceback (most recent call last):\r\n"
        b'  File "main.py", line 12\r\n'
        b"RuntimeError: buzzer failed\r\n"
    )
    CaptureSerial.chunks = [raw_bytes]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)

    def fail_persistence(*_args, **_kwargs):
        raise serial_tools.SerialCapturePersistError(
            None,
            "open",
            OSError("forced open failure"),
        )

    monkeypatch.setattr(serial_tools, "_write_raw_capture", fail_persistence)
    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=True,
        session_name="double-error",
    )
    scope = LogScope.active()
    errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    )
    completion = next(
        event
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=result["run_id"],
            tail=10,
        )
        if event["phase"] == "complete"
    )

    assert result["ok"] is False
    assert {entry["error_kind"] for entry in errors} == {
        "serial_capture_persist_failed",
        "micropython_traceback",
    }
    assert len({entry["error_id"] for entry in errors}) == 2
    assert {entry["created_at"] for entry in errors} == {completion["ts"]}
