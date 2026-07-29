from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4

from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.migrations import init_database
from esp_mcp_toolchain.tools import log_tools


def _project_snapshot(project_dir: Path) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        metadata = path.stat()
        snapshot[path.relative_to(project_dir).as_posix()] = (
            len(payload),
            metadata.st_mtime_ns,
            hashlib.sha256(payload).hexdigest(),
        )
    return snapshot


def _create_run(
    scope: log_tools.LogScope,
    *,
    project_id: str,
    run_id: str,
) -> None:
    log_repository.create_run(
        scope.database_file,
        project_id=project_id,
        run_id=run_id,
        task_type="log_details",
        selected_port=None,
        started_at="2026-07-29T00:00:00+00:00",
        summary=None,
        payload={},
    )
    log_repository.append_event(
        scope.database_file,
        project_id=project_id,
        run_id=run_id,
        event_uuid=str(uuid4()),
        ts="2026-07-29T00:00:01+00:00",
        phase="complete",
        level="info",
        tool="log_details",
        source="pytest",
        message=f"details for {project_id}",
        payload={},
    )


def _register_raw(
    scope: log_tools.LogScope,
    *,
    project_id: str,
    run_id: str,
    path: str,
    created_at: str,
) -> dict:
    record, inserted = log_repository.register_raw_log(
        scope.database_file,
        project_id=project_id,
        raw_log_id=str(uuid4()),
        run_id=run_id,
        kind="serial_capture_raw",
        path=path,
        created_at=created_at,
        sha256="a" * 64,
    )
    assert inserted is True
    return record


def _register_error(
    scope: log_tools.LogScope,
    *,
    project_id: str,
    run_id: str,
    created_at: str,
    message: str,
    raw_text: str,
) -> dict:
    record, inserted = log_repository.register_error(
        scope.database_file,
        project_id=project_id,
        error_id=str(uuid4()),
        run_id=run_id,
        error_kind="micropython_traceback",
        file="main.py",
        line=7,
        column=3,
        exception_type="ValueError",
        message=message,
        raw_text=raw_text,
        recoverable=False,
        created_at=created_at,
    )
    assert inserted is True
    return record


def test_v3_get_returns_sqlite_artifact_details_and_preserves_legacy_shape():
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "artifact-details"
    _create_run(scope, project_id=scope.project_id, run_id=run_id)
    _create_run(scope, project_id="foreign-project", run_id=run_id)

    second_raw = _register_raw(
        scope,
        project_id=scope.project_id,
        run_id=run_id,
        path="logs/raw/second.bin",
        created_at="2026-07-29T00:00:03+00:00",
    )
    first_raw = _register_raw(
        scope,
        project_id=scope.project_id,
        run_id=run_id,
        path="logs/raw/first.bin",
        created_at="2026-07-29T00:00:02+00:00",
    )
    foreign_raw = _register_raw(
        scope,
        project_id="foreign-project",
        run_id=run_id,
        path="logs/raw/foreign.bin",
        created_at="2026-07-29T00:00:01+00:00",
    )
    second_error = _register_error(
        scope,
        project_id=scope.project_id,
        run_id=run_id,
        created_at="2026-07-29T00:00:05+00:00",
        message="second error",
        raw_text="second traceback",
    )
    first_error = _register_error(
        scope,
        project_id=scope.project_id,
        run_id=run_id,
        created_at="2026-07-29T00:00:04+00:00",
        message="first error",
        raw_text="first traceback",
    )
    foreign_error = _register_error(
        scope,
        project_id="foreign-project",
        run_id=run_id,
        created_at="2026-07-29T00:00:01+00:00",
        message="foreign error",
        raw_text="foreign traceback",
    )

    result = log_tools.esp_logs_get(run_id)

    assert result["ok"] is True
    assert result["project_id"] == scope.project_id
    assert result["run_id"] == run_id
    assert result["run"]["run_id"] == run_id
    assert [event["message"] for event in result["events"]] == [
        f"details for {scope.project_id}"
    ]
    assert [record["raw_log_id"] for record in result["raw_logs"]] == [
        first_raw["raw_log_id"],
        second_raw["raw_log_id"],
    ]
    assert foreign_raw["raw_log_id"] not in {
        record["raw_log_id"] for record in result["raw_logs"]
    }
    assert [record["error_id"] for record in result["errors"]] == [
        first_error["error_id"],
        second_error["error_id"],
    ]
    assert foreign_error["error_id"] not in {
        record["error_id"] for record in result["errors"]
    }
    assert result["query_source"] == {
        "kind": "sqlite",
        "schema_version": 3,
        "authoritative": True,
    }
    assert result["artifact_capability"] == {
        "available": True,
        "raw_logs": True,
        "errors": True,
        "reason": None,
    }
    assert result["artifact_truncation"]["raw_logs"] == {
        "limit": 1000,
        "returned": 2,
        "truncated": False,
    }
    assert result["artifact_truncation"]["errors"] == {
        "limit": 200,
        "returned": 2,
        "truncated": False,
        "fields_truncated": False,
        "field_limits": {
            "file": 4096,
            "exception_type": 256,
            "message": 2048,
            "raw_text": 8192,
        },
    }
    assert all(
        error["field_truncation"]
        == {
            "file": False,
            "exception_type": False,
            "message": False,
            "raw_text": False,
        }
        for error in result["errors"]
    )


def test_v3_get_selects_latest_bounded_artifacts_and_truncates_error_text(
    monkeypatch,
):
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "bounded-details"
    _create_run(scope, project_id=scope.project_id, run_id=run_id)
    monkeypatch.setattr(log_tools, "RAW_LOG_DETAIL_LIMIT", 2, raising=False)
    monkeypatch.setattr(log_tools, "ERROR_DETAIL_LIMIT", 2, raising=False)
    monkeypatch.setattr(log_tools, "ERROR_FILE_CHAR_LIMIT", 10, raising=False)
    monkeypatch.setattr(log_tools, "ERROR_EXCEPTION_TYPE_CHAR_LIMIT", 6, raising=False)
    monkeypatch.setattr(log_tools, "ERROR_MESSAGE_CHAR_LIMIT", 8, raising=False)
    monkeypatch.setattr(log_tools, "ERROR_RAW_TEXT_CHAR_LIMIT", 12, raising=False)

    raw_records = [
        _register_raw(
            scope,
            project_id=scope.project_id,
            run_id=run_id,
            path=f"logs/raw/item-{index}.bin",
            created_at=f"2026-07-29T00:00:0{index}+00:00",
        )
        for index in range(1, 4)
    ]
    error_records = [
        _register_error(
            scope,
            project_id=scope.project_id,
            run_id=run_id,
            created_at=f"2026-07-29T00:00:1{index}+00:00",
            message=("m" * 40 if index == 2 else f"error-{index}"),
            raw_text=("r" * 40 if index == 2 else f"trace-{index}"),
        )
        for index in range(1, 4)
    ]

    result = log_tools.esp_logs_get(run_id)

    assert [record["raw_log_id"] for record in result["raw_logs"]] == [
        raw_records[1]["raw_log_id"],
        raw_records[2]["raw_log_id"],
    ]
    assert [record["error_id"] for record in result["errors"]] == [
        error_records[1]["error_id"],
        error_records[2]["error_id"],
    ]
    assert result["artifact_truncation"]["raw_logs"] == {
        "limit": 2,
        "returned": 2,
        "truncated": True,
    }
    assert result["artifact_truncation"]["errors"] == {
        "limit": 2,
        "returned": 2,
        "truncated": True,
        "fields_truncated": True,
        "field_limits": {
            "file": 10,
            "exception_type": 6,
            "message": 8,
            "raw_text": 12,
        },
    }
    truncated_error = result["errors"][0]
    assert truncated_error["message"] == "m" * 8
    assert truncated_error["raw_text"] == "r" * 12
    assert truncated_error["exception_type"] == "ValueE"
    assert truncated_error["field_truncation"] == {
        "file": False,
        "exception_type": True,
        "message": True,
        "raw_text": True,
    }


def test_v2_get_reports_no_formal_artifact_capability_without_migration():
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "v2-details"
    _create_run(scope, project_id=scope.project_id, run_id=run_id)
    _register_raw(
        scope,
        project_id=scope.project_id,
        run_id=run_id,
        path="logs/raw/v2-bait.bin",
        created_at="2026-07-29T00:00:02+00:00",
    )
    _register_error(
        scope,
        project_id=scope.project_id,
        run_id=run_id,
        created_at="2026-07-29T00:00:03+00:00",
        message="v2 bait",
        raw_text="v2 bait traceback",
    )
    connection = sqlite3.connect(scope.database_file)
    try:
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    before = _project_snapshot(scope.project_dir)

    result = log_tools.esp_logs_get(run_id)

    connection = sqlite3.connect(
        f"file:{scope.database_file.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()
    after = _project_snapshot(scope.project_dir)
    coordination = {
        f"{scope.database_file.name}-wal",
        f"{scope.database_file.name}-shm",
    }

    assert result["ok"] is True
    assert result["run"]["run_id"] == run_id
    assert result["events"]
    assert result["raw_logs"] == []
    assert result["errors"] == []
    assert result["query_source"] == {
        "kind": "sqlite",
        "schema_version": 2,
        "authoritative": True,
    }
    assert result["artifact_capability"] == {
        "available": False,
        "raw_logs": False,
        "errors": False,
        "reason": "schema_v2",
    }
    assert result["artifact_truncation"]["raw_logs"]["returned"] == 0
    assert result["artifact_truncation"]["raw_logs"]["truncated"] is False
    assert result["artifact_truncation"]["errors"]["returned"] == 0
    assert result["artifact_truncation"]["errors"]["truncated"] is False
    assert version == 2
    assert {
        path: metadata
        for path, metadata in after.items()
        if path not in coordination
    } == {
        path: metadata
        for path, metadata in before.items()
        if path not in coordination
    }
    assert not scope.log_root.exists()


def test_v3_get_rejects_noncanonical_stored_raw_path():
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "invalid-raw-path"
    _create_run(scope, project_id=scope.project_id, run_id=run_id)
    connection = sqlite3.connect(scope.database_file)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            INSERT INTO raw_logs (
              project_id, raw_log_id, run_id, kind, path, created_at, sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope.project_id,
                str(uuid4()),
                run_id,
                "serial_capture_raw",
                "../escape.bin",
                "2026-07-29T00:00:02+00:00",
                "a" * 64,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    result = log_tools.esp_logs_get(run_id)

    assert result["ok"] is False
    assert result["error_kind"] == "log_database_invalid"
    assert result["recoverable"] is False


def test_v3_get_reads_run_events_and_artifacts_from_one_snapshot(monkeypatch):
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "single-snapshot-details"
    _create_run(scope, project_id=scope.project_id, run_id=run_id)
    initial_raw = _register_raw(
        scope,
        project_id=scope.project_id,
        run_id=run_id,
        path="logs/raw/initial.bin",
        created_at="2026-07-29T00:00:02+00:00",
    )
    initial_error = _register_error(
        scope,
        project_id=scope.project_id,
        run_id=run_id,
        created_at="2026-07-29T00:00:03+00:00",
        message="initial error",
        raw_text="initial traceback",
    )
    original = log_repository.list_recent_raw_logs_for_run
    concurrent_raw_id = str(uuid4())
    concurrent_error_id = str(uuid4())

    def insert_after_snapshot(connection, **kwargs):
        writer = sqlite3.connect(scope.database_file)
        try:
            writer.execute("PRAGMA foreign_keys = ON")
            writer.execute(
                """
                INSERT INTO raw_logs (
                  project_id, raw_log_id, run_id, kind, path, created_at, sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.project_id,
                    concurrent_raw_id,
                    run_id,
                    "serial_capture_raw",
                    "logs/raw/concurrent.bin",
                    "2026-07-29T00:00:04+00:00",
                    "b" * 64,
                ),
            )
            writer.execute(
                """
                INSERT INTO errors (
                  project_id, error_id, run_id, error_kind, file, line, column,
                  exception_type, message, raw_text, recoverable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.project_id,
                    concurrent_error_id,
                    run_id,
                    "micropython_traceback",
                    "main.py",
                    8,
                    1,
                    "RuntimeError",
                    "concurrent error",
                    "concurrent traceback",
                    0,
                    "2026-07-29T00:00:05+00:00",
                ),
            )
            writer.commit()
        finally:
            writer.close()
        return original(connection, **kwargs)

    monkeypatch.setattr(
        log_repository,
        "list_recent_raw_logs_for_run",
        insert_after_snapshot,
    )

    result = log_tools.esp_logs_get(run_id)

    assert [record["raw_log_id"] for record in result["raw_logs"]] == [
        initial_raw["raw_log_id"]
    ]
    assert [record["error_id"] for record in result["errors"]] == [
        initial_error["error_id"]
    ]
    assert concurrent_raw_id in {
        record["raw_log_id"]
        for record in log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    }
    assert concurrent_error_id in {
        record["error_id"]
        for record in log_repository.get_run_errors(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    }


def test_v3_get_rejects_noncanonical_stored_error_value():
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "invalid-error-value"
    _create_run(scope, project_id=scope.project_id, run_id=run_id)
    connection = sqlite3.connect(scope.database_file)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            INSERT INTO errors (
              project_id, error_id, run_id, error_kind, file, line, column,
              exception_type, message, raw_text, recoverable, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope.project_id,
                str(uuid4()),
                run_id,
                "micropython_traceback",
                "main.py",
                1,
                1,
                "ValueError",
                "invalid recoverable",
                "traceback",
                2,
                "2026-07-29T00:00:02+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    result = log_tools.esp_logs_get(run_id)

    assert result["ok"] is False
    assert result["error_kind"] == "log_database_invalid"
    assert result["recoverable"] is False
