from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

import pytest

import esp_mcp_toolchain
from esp_mcp_toolchain.backends.serial_monitor_store import recover_serial_runs
from esp_mcp_toolchain.config import get_selected_port, set_selected_port
from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.db import CURRENT_SCHEMA_VERSION, connect
from esp_mcp_toolchain.database.migrations import init_database
from esp_mcp_toolchain.paths import logs_dir
from esp_mcp_toolchain.project_context import get_project_context, select_project_context
from esp_mcp_toolchain.server import create_mcp_server
from esp_mcp_toolchain.tools import log_tools, port_tools, serial_tools
from esp_mcp_toolchain.tools.log_tools import (
    LogScope,
    esp_logs_get,
    esp_logs_latest,
    esp_logs_query,
    finish_run,
    import_legacy_jsonl,
    logged_task,
    start_run,
    write_event,
)


def test_cross_worktree_gate_loads_requested_main_source():
    source_root = os.environ.get("ESP_MCP_SOURCE_ROOT")
    if source_root:
        package_path = Path(esp_mcp_toolchain.__file__).resolve()
        assert Path(source_root).resolve() / "toolchain" in package_path.parents


def test_schema_v2_has_required_columns_constraints_and_indexes():
    scope = LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    connection = connect(scope.database_file)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        run_columns = {row["name"]: row for row in connection.execute("PRAGMA table_info(runs)")}
        event_columns = {row["name"]: row for row in connection.execute("PRAGMA table_info(events)")}
        assert {
            "project_id",
            "run_id",
            "task_type",
            "status",
            "started_at",
            "ended_at",
            "next_sequence_no",
            "selected_port",
            "summary",
            "payload_json",
        } <= set(run_columns)
        assert {
            "event_uuid",
            "project_id",
            "run_id",
            "sequence_no",
            "ts",
            "phase",
            "level",
            "tool",
            "source",
            "message",
            "payload_json",
        } <= set(event_columns)
        assert run_columns["project_id"]["notnull"] == 1
        assert event_columns["sequence_no"]["notnull"] == 1
        index_names = {row["name"] for row in connection.execute("PRAGMA index_list(events)")}
        assert "idx_events_project_phase_ts" in index_names
        assert "idx_events_project_level_ts" in index_names
        assert "idx_events_project_tool_ts" in index_names
        foreign_keys = connection.execute("PRAGMA foreign_key_list(events)").fetchall()
        assert {row["from"] for row in foreign_keys} == {"project_id", "run_id"}
    finally:
        connection.close()


def test_sequence_numbers_are_atomic_and_order_same_timestamp_events():
    scope = LogScope.active()
    run = start_run("concurrent_test", run_id="shared-sequence")
    timestamp = "2026-07-20T00:00:00+00:00"

    def append(index: int):
        return log_repository.append_event(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run["run_id"],
            event_uuid=str(uuid4()),
            ts=timestamp,
            phase="execute",
            level="info",
            tool="concurrent_test",
            source="pytest",
            message=f"event-{index}",
            payload={"index": index},
        )[0]

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = list(executor.map(append, range(16)))

    assert sorted(event["sequence_no"] for event in events) == list(range(1, 17))
    stored = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        tail=100,
    )
    assert [event["sequence_no"] for event in stored] == list(range(1, 17))


def test_event_uuid_retry_is_idempotent_and_conflicting_content_is_rejected():
    event_uuid = str(uuid4())
    run = start_run("uuid_test", run_id="uuid-run")
    timestamp = "2026-07-20T00:00:00+00:00"
    first = write_event(
        "uuid_test",
        "info",
        "same event",
        {"value": 1},
        run_id=run["run_id"],
        phase="verify",
        event_uuid=event_uuid,
        source="pytest",
        ts=timestamp,
    )
    retry = write_event(
        "uuid_test",
        "info",
        "same event",
        {"value": 1},
        run_id=run["run_id"],
        phase="verify",
        event_uuid=event_uuid,
        source="pytest",
        ts=timestamp,
    )
    conflict = write_event(
        "uuid_test",
        "info",
        "changed event",
        {"value": 2},
        run_id=run["run_id"],
        phase="verify",
        event_uuid=event_uuid,
        source="pytest",
        ts=timestamp,
    )
    timestamp_conflict = write_event(
        "uuid_test",
        "info",
        "same event",
        {"value": 1},
        run_id=run["run_id"],
        phase="verify",
        event_uuid=event_uuid,
        source="pytest",
        ts="2026-07-20T00:00:01+00:00",
    )

    assert first["deduplicated"] is False
    assert retry["deduplicated"] is True
    assert retry["sequence_no"] == first["sequence_no"]
    assert conflict["ok"] is False
    assert conflict["error_kind"] == "event_uuid_conflict"
    assert timestamp_conflict["ok"] is False
    assert timestamp_conflict["error_kind"] == "event_uuid_conflict"
    result = esp_logs_get(run["run_id"])
    assert len(result["events"]) == 1

def test_query_filters_match_written_columns_and_latest_comes_from_sqlite():
    run = start_run("query_test", run_id="query-run", selected_port="COM_TEST")
    write_event(
        "esp_query_probe",
        "warning",
        "Structured probe matched",
        {"category": "probe"},
        run_id=run["run_id"],
        phase="verify",
        source="pytest",
    )
    write_event(
        "esp_query_probe",
        "info",
        "Cleanup finished",
        {},
        run_id=run["run_id"],
        phase="cleanup",
        source="pytest",
    )
    finish_run(run["run_id"], "succeeded", summary="query complete")

    result = esp_logs_query(
        "Structured probe",
        run_id=run["run_id"],
        phase="verify",
        level="warning",
        tool="esp_query_probe",
        source="pytest",
        from_ts="2000-01-01T00:00:00+00:00",
        to_ts="2100-01-01T00:00:00+00:00",
        sequence_from=1,
        sequence_to=1,
    )
    latest = esp_logs_latest()

    assert result["ok"] is True
    assert len(result["matches"]) == 1
    assert result["matches"][0]["payload_json"] == {"category": "probe"}
    assert latest["latest"]["run_id"] == run["run_id"]
    assert latest["latest"]["status"] == "succeeded"


def test_same_run_id_cannot_cross_project_boundary(tmp_path):
    first_workspace = tmp_path / "first-workspace"
    second_workspace = tmp_path / "second-workspace"
    first_workspace.mkdir()
    second_workspace.mkdir()

    first_context = select_project_context(first_workspace)
    start_run("isolation_test", run_id="same-run")
    write_event("isolation_test", "info", "first-only", run_id="same-run")
    first_database = LogScope.active().database_file

    second_context = select_project_context(second_workspace)
    start_run("isolation_test", run_id="same-run")
    write_event("isolation_test", "info", "second-only", run_id="same-run")
    second_result = esp_logs_get("same-run")

    select_project_context(first_workspace)
    first_result = esp_logs_get("same-run")

    assert first_context["project_id"] != second_context["project_id"]
    assert first_database != LogScope.bound(
        project_id=second_context["project_id"],
        log_root=Path(second_context["project_dir"]) / "logs",
    ).database_file
    assert [event["message"] for event in first_result["events"]] == ["first-only"]
    assert [event["message"] for event in second_result["events"]] == ["second-only"]


def test_legacy_jsonl_import_is_repeatable_and_marks_unknown_phase():
    session = logs_dir() / "sessions" / "legacy-run.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "event_id": "evt_legacy_1",
            "run_id": "legacy-run",
            "ts": "2026-07-13T00:00:00+00:00",
            "tool": "legacy_tool",
            "level": "serial",
            "source": "legacy",
            "message": "first",
            "data": {"port": "COM3"},
        },
        {
            "event_id": "evt_legacy_2",
            "run_id": "legacy-run",
            "ts": "2026-07-13T00:00:00+00:00",
            "tool": "legacy_tool",
            "level": "error",
            "source": "legacy",
            "message": "second",
            "data": {},
        },
    ]
    session.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")

    first = import_legacy_jsonl()
    second = import_legacy_jsonl()
    result = esp_logs_get("legacy-run")

    assert first["events_imported"] == 2
    assert second["events_imported"] == 0
    assert [event["sequence_no"] for event in result["events"]] == [1, 2]
    assert {event["phase"] for event in result["events"]} == {"unknown"}
    assert result["run"]["status"] == "failed"
    for event in result["events"]:
        assert str(UUID(event["event_uuid"])) == event["event_uuid"]


def test_legacy_v1_schema_migrates_without_losing_log_rows(tmp_path):
    legacy_database = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(legacy_database)
    try:
        connection.executescript(
            """
            CREATE TABLE runs (
              run_id TEXT PRIMARY KEY,
              started_at TEXT NOT NULL,
              ended_at TEXT,
              status TEXT NOT NULL,
              summary TEXT,
              selected_port TEXT,
              project_dir TEXT
            );
            CREATE TABLE events (
              event_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              ts TEXT NOT NULL,
              tool TEXT,
              level TEXT,
              source TEXT,
              message TEXT,
              data_json TEXT,
              FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            """
        )
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("legacy", "2026-07-13T00:00:00+00:00", None, "ok", "old", "COM3", "old-root"),
        )
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evt_old",
                "legacy",
                "2026-07-13T00:00:00+00:00",
                "legacy_tool",
                "serial",
                "legacy",
                "old event",
                '{"value":1}',
            ),
        )
        connection.commit()
    finally:
        connection.close()

    init_database(legacy_database, project_id="legacy-project")
    migrated = connect(legacy_database)
    try:
        run = migrated.execute("SELECT * FROM runs").fetchone()
        event = migrated.execute("SELECT * FROM events").fetchone()
        assert run["project_id"] == "legacy-project"
        assert run["status"] == "succeeded"
        assert run["next_sequence_no"] == 2
        assert event["phase"] == "unknown"
        assert event["level"] == "info"
        assert json.loads(event["payload_json"]) == {"value": 1}
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        migrated.close()


def test_mcp_log_query_schema_exposes_structured_filters():
    tools = asyncio.run(create_mcp_server().list_tools())
    query_tool = next(tool for tool in tools if tool.name == "esp_logs_query")
    assert {
        "query",
        "limit",
        "level",
        "run_id",
        "phase",
        "tool",
        "source",
        "from_ts",
        "to_ts",
        "sequence_from",
        "sequence_to",
    } <= set(query_tool.inputSchema["properties"])


def test_nested_logged_tasks_share_one_run():
    @logged_task(task_type="inner")
    def inner_task() -> dict:
        return {"ok": True, "message": "inner completed"}

    @logged_task(task_type="outer")
    def outer_task() -> dict:
        nested = inner_task()
        assert nested["ok"] is True
        return {"ok": True, "message": "outer completed"}

    result = outer_task()

    assert result["ok"] is True
    run_id = result["run_id"]
    database = Path(get_project_context()["project_dir"]) / "esp_mcp.sqlite"
    with sqlite3.connect(database) as connection:
        runs = connection.execute(
            "SELECT run_id, task_type, status FROM runs ORDER BY run_id"
        ).fetchall()
        events = connection.execute(
            """
            SELECT run_id, sequence_no, phase, tool
            FROM events
            ORDER BY sequence_no
            """
        ).fetchall()

    assert runs == [(run_id, "outer", "succeeded")]
    assert events == [
        (run_id, 1, "prepare", "outer_task"),
        (run_id, 2, "complete", "outer_task"),
    ]

def test_terminal_run_finish_and_event_retries_are_strictly_idempotent():
    scope = LogScope.active()
    run = start_run("terminal_contract", run_id="terminal-run")
    timestamp = "2026-07-20T00:00:00+00:00"
    event_uuid = str(uuid4())
    first_event, inserted = log_repository.append_event(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        event_uuid=event_uuid,
        ts=timestamp,
        phase="complete",
        level="info",
        tool="terminal_contract",
        source="pytest",
        message="completed once",
        payload={"attempt": 1},
    )
    assert inserted is True

    first_finish = log_repository.finish_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        status="succeeded",
        ended_at="2026-07-20T00:00:01+00:00",
        summary="first terminal result",
        payload={"finish_attempt": 1},
    )
    retry_finish = log_repository.finish_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        status="succeeded",
        ended_at="2026-07-20T00:00:02+00:00",
        summary="must not replace the first result",
        payload={"finish_attempt": 2},
    )

    assert retry_finish == first_finish
    with pytest.raises(log_repository.RunStateConflictError):
        log_repository.finish_run(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run["run_id"],
            status="failed",
            ended_at="2026-07-20T00:00:03+00:00",
            summary="conflicting terminal result",
        )

    retried_event, retried_inserted = log_repository.append_event(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        event_uuid=event_uuid,
        ts=timestamp,
        phase="complete",
        level="info",
        tool="terminal_contract",
        source="pytest",
        message="completed once",
        payload={"attempt": 1},
    )
    assert retried_inserted is False
    assert retried_event == first_event

    with pytest.raises(log_repository.RunNotRunningError):
        log_repository.append_event(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run["run_id"],
            event_uuid=str(uuid4()),
            ts="2026-07-20T00:00:04+00:00",
            phase="complete",
            level="info",
            tool="terminal_contract",
            source="pytest",
            message="late new event",
            payload={},
        )


def test_create_run_allows_only_null_to_concrete_selected_port_update():
    scope = LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    create_args = {
        "project_id": scope.project_id,
        "run_id": "selected-port-run",
        "task_type": "selected_port_contract",
        "started_at": "2026-07-20T00:00:00+00:00",
        "summary": "selected port contract",
        "payload": {},
    }

    created, was_created = log_repository.create_run(
        scope.database_file,
        selected_port=None,
        **create_args,
    )
    updated, was_updated = log_repository.create_run(
        scope.database_file,
        selected_port="COM3",
        **create_args,
    )
    same, was_recreated = log_repository.create_run(
        scope.database_file,
        selected_port="COM3",
        **create_args,
    )

    assert was_created is True
    assert created["selected_port"] is None
    assert was_updated is False
    assert updated["selected_port"] == "COM3"
    assert was_recreated is False
    assert same["selected_port"] == "COM3"
    with pytest.raises(log_repository.RunConflictError):
        log_repository.create_run(
            scope.database_file,
            selected_port="COM4",
            **create_args,
        )


def test_copied_legacy_jsonl_deduplicates_the_same_event():
    sessions = logs_dir() / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    record = {
        "event_id": "evt_copied_once",
        "run_id": "copied-legacy-run",
        "ts": "2026-07-13T00:00:00+00:00",
        "phase": "execute",
        "tool": "legacy_copy",
        "level": "info",
        "source": "legacy",
        "message": "same copied record",
        "data": {"value": 1},
    }
    serialized = json.dumps(record, ensure_ascii=False) + "\n"
    (sessions / "copy-a.jsonl").write_text(serialized, encoding="utf-8")
    (sessions / "copy-b.jsonl").write_text(serialized, encoding="utf-8")

    imported = import_legacy_jsonl()
    result = esp_logs_get("copied-legacy-run")

    assert imported["files_imported"] == 2
    assert imported["events_imported"] == 1
    assert imported["events_deduplicated"] == 1
    assert len(result["events"]) == 1


def test_importing_native_running_jsonl_mirror_does_not_finish_the_run():
    scope = LogScope.active()
    run = start_run("native_mirror", run_id="native-running")
    event = write_event(
        "native_mirror",
        "info",
        "native event",
        {"value": 1},
        run_id=run["run_id"],
        ts="2026-07-20T00:00:00+00:00",
        phase="execute",
        event_uuid=str(uuid4()),
        source="pytest",
    )
    assert event.get("ok") is not False

    imported = import_legacy_jsonl()
    stored_run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    )
    stored_events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        tail=10,
    )

    assert imported["events_imported"] == 0
    assert imported["events_deduplicated"] == 1
    assert stored_run is not None
    assert stored_run["status"] == "running"
    assert len(stored_events) == 1


def test_v1_project_tables_rebuild_composite_keys_and_project_scoped_uniqueness(tmp_path):
    legacy_database = tmp_path / "legacy-project-tables.sqlite"
    connection = sqlite3.connect(legacy_database)
    try:
        connection.executescript(
            """
            CREATE TABLE hardwork_items (
              hardwork_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              title TEXT NOT NULL,
              raw_path TEXT,
              processed_path TEXT,
              source TEXT,
              confidence REAL,
              created_at TEXT NOT NULL,
              updated_at TEXT
            );
            CREATE TABLE hardwork_audit (
              audit_id TEXT PRIMARY KEY,
              hardwork_id TEXT,
              action TEXT NOT NULL,
              old_value_json TEXT,
              new_value_json TEXT,
              reason TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE memory_items (
              memory_id TEXT PRIMARY KEY,
              namespace TEXT NOT NULL,
              key TEXT NOT NULL,
              value TEXT NOT NULL,
              memory_type TEXT NOT NULL,
              source TEXT NOT NULL,
              confidence REAL NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT,
              UNIQUE(namespace, key)
            );
            CREATE TABLE memory_audit (
              audit_id TEXT PRIMARY KEY,
              memory_id TEXT,
              action TEXT NOT NULL,
              old_value TEXT,
              new_value TEXT,
              reason TEXT,
              created_at TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        connection.execute(
            "INSERT INTO hardwork_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "shared-hardwork",
                "note",
                "legacy hardwork",
                None,
                None,
                "legacy",
                0.5,
                "2026-07-13T00:00:00+00:00",
                None,
            ),
        )
        connection.execute(
            "INSERT INTO hardwork_audit VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "shared-hardwork-audit",
                "shared-hardwork",
                "create",
                None,
                "{}",
                "legacy",
                "2026-07-13T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO memory_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "shared-memory",
                "shared-namespace",
                "shared-key",
                "legacy value",
                "fact",
                "legacy",
                0.5,
                "active",
                "2026-07-13T00:00:00+00:00",
                None,
            ),
        )
        connection.execute(
            "INSERT INTO memory_audit VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "shared-memory-audit",
                "shared-memory",
                "create",
                None,
                "legacy value",
                "legacy",
                "2026-07-13T00:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    init_database(legacy_database, project_id="project-a")
    migrated = connect(legacy_database)
    try:
        expected_primary_keys = {
            "hardwork_items": ("project_id", "hardwork_id"),
            "hardwork_audit": ("project_id", "audit_id"),
            "memory_items": ("project_id", "memory_id"),
            "memory_audit": ("project_id", "audit_id"),
        }
        for table, expected_primary_key in expected_primary_keys.items():
            table_info = migrated.execute(f'PRAGMA table_info("{table}")').fetchall()
            columns = {row["name"]: row for row in table_info}
            primary_key = tuple(
                row["name"]
                for row in sorted(table_info, key=lambda item: item["pk"])
                if row["pk"] > 0
            )
            assert columns["project_id"]["notnull"] == 1
            assert primary_key == expected_primary_key

        assert migrated.execute(
            "SELECT project_id FROM hardwork_items WHERE hardwork_id = ?",
            ("shared-hardwork",),
        ).fetchone()[0] == "project-a"
        assert migrated.execute(
            "SELECT project_id FROM memory_items WHERE memory_id = ?",
            ("shared-memory",),
        ).fetchone()[0] == "project-a"

        migrated.execute(
            """
            INSERT INTO hardwork_items (
              project_id, hardwork_id, kind, title, source, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "project-b",
                "shared-hardwork",
                "note",
                "project-b hardwork",
                "pytest",
                1.0,
                "2026-07-20T00:00:00+00:00",
            ),
        )
        migrated.execute(
            """
            INSERT INTO memory_items (
              project_id, memory_id, namespace, key, value, memory_type,
              source, confidence, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "project-b",
                "shared-memory",
                "shared-namespace",
                "shared-key",
                "project-b value",
                "fact",
                "pytest",
                1.0,
                "active",
                "2026-07-20T00:00:00+00:00",
            ),
        )
        migrated.commit()

        assert migrated.execute(
            "SELECT COUNT(*) FROM hardwork_items WHERE hardwork_id = ?",
            ("shared-hardwork",),
        ).fetchone()[0] == 2
        assert migrated.execute(
            """
            SELECT COUNT(*) FROM memory_items
            WHERE namespace = ? AND key = ?
            """,
            ("shared-namespace", "shared-key"),
        ).fetchone()[0] == 2
    finally:
        migrated.close()


def test_logged_task_preserves_success_when_completion_event_write_fails(monkeypatch):
    original_write_event = log_tools.write_event

    def fail_completion_event(*args, **kwargs):
        if kwargs.get("phase") == "complete":
            return {
                "ok": False,
                "error_kind": "forced_completion_write_failure",
                "message": "forced completion write failure",
            }
        return original_write_event(*args, **kwargs)

    monkeypatch.setattr(log_tools, "write_event", fail_completion_event)

    @logged_task(task_type="post_write_failure")
    def successful_action() -> dict:
        return {"ok": True, "message": "hardware action succeeded", "value": 42}

    result = successful_action()

    assert result["ok"] is True
    assert result["message"] == "hardware action succeeded"
    assert result["value"] == 42
    assert result["logging_persisted"] is False
    assert "forced completion write failure" in result["logging_warning"]


def test_logged_task_preserves_success_when_run_finish_fails(monkeypatch):
    def fail_finish(*args, **kwargs):
        raise RuntimeError("forced run finish failure")

    monkeypatch.setattr(log_tools, "finish_run", fail_finish)

    @logged_task(task_type="post_finish_failure")
    def successful_action() -> dict:
        return {"ok": True, "message": "hardware action succeeded", "value": 84}

    result = successful_action()

    assert result["ok"] is True
    assert result["message"] == "hardware action succeeded"
    assert result["value"] == 84
    assert result["logging_persisted"] is False
    assert "forced run finish failure" in result["logging_warning"]

def test_repository_query_normalizes_equivalent_timezone_bounds_and_rejects_reverse_range():
    scope = LogScope.active()
    run = start_run("timezone_query", run_id="timezone-query-run")
    event_uuid = str(uuid4())
    written = write_event(
        "timezone_query",
        "info",
        "timezone boundary event",
        {},
        run_id=run["run_id"],
        ts="2026-07-20T00:00:00+00:00",
        phase="verify",
        event_uuid=event_uuid,
        source="pytest",
    )
    assert written.get("ok") is not False

    matches = log_repository.query_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        from_ts="2026-07-20T08:00:00+08:00",
        to_ts="2026-07-20T00:00:00Z",
    )

    assert [event["event_uuid"] for event in matches] == [event_uuid]
    with pytest.raises(log_repository.EventRepositoryError, match="from_ts must not exceed to_ts"):
        log_repository.query_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run["run_id"],
            from_ts="2026-07-20T00:00:01Z",
            to_ts="2026-07-20T00:00:00+00:00",
        )


def test_legacy_jsonl_unknown_level_imports_as_info_and_preserves_raw_level():
    session = logs_dir() / "sessions" / "legacy-notice.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event_id": "evt_legacy_notice",
        "run_id": "legacy-notice-run",
        "ts": "2026-07-13T00:00:00Z",
        "phase": "complete",
        "tool": "legacy_notice",
        "level": "notice",
        "source": "legacy",
        "message": "legacy notice event",
        "data": {"value": 1},
    }
    session.write_text(json.dumps(record) + "\n", encoding="utf-8")

    imported = import_legacy_jsonl()
    result = esp_logs_get("legacy-notice-run")

    assert imported["events_imported"] == 1
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["level"] == "info"
    assert event["payload_json"] == {"value": 1, "legacy_level": "notice"}


def test_growing_legacy_jsonl_stays_running_until_complete_event_arrives():
    scope = LogScope.active()
    session = logs_dir() / "sessions" / "growing-legacy.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    prepare_record = {
        "event_id": "evt_growing_prepare",
        "run_id": "growing-legacy-run",
        "ts": "2026-07-20T00:00:00Z",
        "phase": "prepare",
        "tool": "legacy_growing",
        "level": "info",
        "source": "legacy",
        "message": "legacy run started",
        "data": {},
    }
    complete_record = {
        "event_id": "evt_growing_complete",
        "run_id": "growing-legacy-run",
        "ts": "2026-07-20T00:00:01Z",
        "phase": "complete",
        "tool": "legacy_growing",
        "level": "info",
        "source": "legacy",
        "message": "legacy run completed",
        "data": {},
    }
    session.write_text(json.dumps(prepare_record) + "\n", encoding="utf-8")

    first_import = import_legacy_jsonl()
    first_run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id="growing-legacy-run",
    )

    assert first_import["events_imported"] == 1
    assert first_run is not None
    assert first_run["status"] == "running"

    session.write_text(
        "\n".join(json.dumps(record) for record in (prepare_record, complete_record)) + "\n",
        encoding="utf-8",
    )
    second_import = import_legacy_jsonl()
    completed_run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id="growing-legacy-run",
    )
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id="growing-legacy-run",
        tail=10,
    )

    assert second_import["events_imported"] == 1
    assert second_import["events_deduplicated"] == 1
    assert [event["phase"] for event in events] == ["prepare", "complete"]
    assert completed_run is not None
    assert completed_run["status"] == "succeeded"


def test_concurrent_legacy_jsonl_import_is_idempotent_and_claims_one_marker():
    scope = LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    session = logs_dir() / "sessions" / "concurrent-import.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event_id": "evt_concurrent_import",
        "run_id": "concurrent-import-run",
        "ts": "2026-07-20T00:00:00Z",
        "phase": "complete",
        "tool": "legacy_concurrent",
        "level": "info",
        "source": "legacy",
        "message": "concurrent import event",
        "data": {},
    }
    session.write_text(json.dumps(record) + "\n", encoding="utf-8")

    def import_once(_index: int):
        return log_repository.import_jsonl_sessions(
            scope.database_file,
            project_id=scope.project_id,
            logs_root=scope.log_root,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(import_once, range(2)))

    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id="concurrent-import-run",
        tail=10,
    )
    connection = connect(scope.database_file)
    try:
        marker_count = connection.execute(
            "SELECT COUNT(*) FROM legacy_jsonl_imports WHERE project_id = ?",
            (scope.project_id,),
        ).fetchone()[0]
    finally:
        connection.close()

    assert sum(result["files_imported"] for result in results) == 1
    assert sum(result["events_imported"] for result in results) == 1
    assert len(events) == 1
    assert marker_count == 1


def test_init_database_is_safe_under_two_threads(tmp_path):
    database = tmp_path / "threaded-init.sqlite"

    def initialize(_index: int):
        return init_database(database, project_id="threaded-init-project")

    with ThreadPoolExecutor(max_workers=2) as executor:
        initialized_paths = list(executor.map(initialize, range(2)))

    assert initialized_paths == [database, database]
    connection = connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (CURRENT_SCHEMA_VERSION,),
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()

@pytest.mark.parametrize("failure_stage", ("completion_event", "finish_run"))
def test_port_select_preserves_success_when_post_action_logging_fails(monkeypatch, failure_stage):
    if failure_stage == "completion_event":
        original_write_event = log_tools.write_event

        def fail_completion_event(*args, **kwargs):
            if kwargs.get("phase") == "complete":
                return {
                    "ok": False,
                    "error_kind": "forced_port_completion_failure",
                    "message": "forced port completion failure",
                }
            return original_write_event(*args, **kwargs)

        monkeypatch.setattr(log_tools, "write_event", fail_completion_event)
        expected_warning = "forced port completion failure"
    else:
        def fail_finish(*args, **kwargs):
            raise RuntimeError("forced port finish failure")

        monkeypatch.setattr(log_tools, "finish_run", fail_finish)
        expected_warning = "forced port finish failure"

    result = port_tools.esp_port_select("COM77", reason="sqlite contract")

    assert get_selected_port() == "COM77"
    assert result["ok"] is True
    assert result["selected_port"] == "COM77"
    assert result["logging_persisted"] is False
    assert expected_warning in result["logging_warning"]

def test_current_format_later_event_backfills_selected_port():
    scope = LogScope.active()
    session = logs_dir() / "sessions" / "current-format-backfill.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    prepare_record = {
        "event_uuid": str(uuid4()),
        "run_id": "current-format-backfill-run",
        "task_type": "esp_serial_monitor",
        "ts": "2026-07-20T00:00:00Z",
        "phase": "prepare",
        "tool": "esp_serial_monitor",
        "level": "info",
        "source": "toolchain",
        "message": "monitor starting",
        "payload_json": {},
        "selected_port": None,
    }
    port_record = {
        "event_uuid": str(uuid4()),
        "run_id": "current-format-backfill-run",
        "task_type": "esp_serial_monitor",
        "ts": "2026-07-20T00:00:01Z",
        "phase": "execute",
        "tool": "esp_serial_monitor",
        "level": "info",
        "source": "toolchain",
        "message": "monitor opened selected port",
        "payload_json": {"state": "RUNNING"},
        "selected_port": "COM12",
    }
    session.write_text(json.dumps(prepare_record) + "\n", encoding="utf-8")

    first_import = import_legacy_jsonl()
    first_run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id="current-format-backfill-run",
    )
    assert first_import["events_imported"] == 1
    assert first_run is not None
    assert first_run["selected_port"] is None
    assert first_run["status"] == "running"

    session.write_text(
        "\n".join(json.dumps(record) for record in (prepare_record, port_record)) + "\n",
        encoding="utf-8",
    )
    second_import = import_legacy_jsonl()
    updated_run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id="current-format-backfill-run",
    )
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id="current-format-backfill-run",
        tail=10,
    )

    assert second_import["events_imported"] == 1
    assert second_import["events_deduplicated"] == 1
    assert updated_run is not None
    assert updated_run["selected_port"] == "COM12"
    assert updated_run["status"] == "running"
    assert len(events) == 2


def test_legacy_stopped_event_without_phase_finishes_run_as_cancelled():
    session = logs_dir() / "sessions" / "legacy-stopped.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event_id": "evt_legacy_stopped",
        "run_id": "legacy-stopped-run",
        "ts": "2026-07-13T00:00:00Z",
        "tool": "esp_serial_monitor",
        "level": "info",
        "source": "legacy",
        "message": "monitor stopped",
        "data": {"state": "STOPPED", "port": "COM13"},
    }
    session.write_text(json.dumps(record) + "\n", encoding="utf-8")

    imported = import_legacy_jsonl()
    result = esp_logs_get("legacy-stopped-run")

    assert imported["events_imported"] == 1
    assert result["run"]["status"] == "cancelled"
    assert result["run"]["selected_port"] == "COM13"
    assert len(result["events"]) == 1
    assert result["events"][0]["phase"] == "unknown"
    assert result["events"][0]["payload_json"]["state"] == "STOPPED"


def test_jsonl_event_mirror_includes_task_type_and_selected_port():
    run = start_run(
        "mirror_metadata_contract",
        run_id="mirror-metadata-run",
        selected_port="COM14",
    )
    event = write_event(
        "mirror_metadata_contract",
        "info",
        "mirror metadata event",
        {"state": "RUNNING"},
        run_id=run["run_id"],
        ts="2026-07-20T00:00:00Z",
        phase="execute",
        event_uuid=str(uuid4()),
        source="pytest",
    )
    assert event.get("ok") is not False

    session = logs_dir() / "sessions" / f"{run['run_id']}.jsonl"
    records = [json.loads(line) for line in session.read_text(encoding="utf-8").splitlines()]

    assert len(records) == 1
    assert records[0]["event_uuid"] == event["event_uuid"]
    assert records[0]["task_type"] == "mirror_metadata_contract"
    assert records[0]["selected_port"] == "COM14"

def test_logged_task_runs_business_after_prepare_mirror_fails_post_commit(monkeypatch):
    original_mirror_event = log_tools._mirror_event
    business_calls: list[str] = []

    def fail_prepare_mirror(event, scope):
        if event["phase"] == "prepare":
            raise OSError("forced prepare mirror failure")
        return original_mirror_event(event, scope)

    monkeypatch.setattr(log_tools, "_mirror_event", fail_prepare_mirror)

    @logged_task(task_type="prepare_mirror_failure")
    def successful_action() -> dict:
        business_calls.append("executed")
        return {"ok": True, "message": "business action succeeded", "value": 126}

    result = successful_action()

    assert business_calls == ["executed"]
    assert result["ok"] is True
    assert result["message"] == "business action succeeded"
    assert result["value"] == 126
    assert result["logging_persisted"] is False
    assert "forced prepare mirror failure" in result["logging_warning"]

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
    assert run is not None
    assert run["status"] == "succeeded"
    assert [event["phase"] for event in events] == ["prepare", "complete"]


def test_monitor_start_terminal_failure_after_prepare_finishes_sqlite_run(monkeypatch):
    class FailedSession:
        def status(self):
            return {
                "state": "FAILED",
                "worker_alive": False,
                "last_error": {
                    "error_kind": "forced_monitor_start_failure",
                    "message": "forced monitor start failure",
                },
            }

        def request_stop(self, _timeout):
            return self.status()

    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: object())
    monkeypatch.setattr(
        serial_tools,
        "describe_serial_port",
        lambda port: {"port": port, "device_path": port},
    )
    monkeypatch.setattr(
        serial_tools.SERIAL_MONITOR_MANAGER,
        "start",
        lambda _binding, _serial_module: FailedSession(),
    )

    result = serial_tools.esp_serial_monitor_start("COM_PREPARE_FAIL")

    assert result["ok"] is False
    assert result["error_kind"] == "forced_monitor_start_failure"
    scope = LogScope.active()
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    )
    assert run is not None
    assert run["status"] == "failed"
    assert run["ended_at"] is not None


def test_stale_monitor_recovery_finishes_bound_sqlite_run_failed():
    scope = LogScope.active()
    run = start_run(
        "serial_monitor",
        run_id="monitor_stale_sqlite",
        selected_port="COM_STALE",
        scope=scope,
    )
    write_event(
        "esp_serial_monitor_start",
        "info",
        "stale monitor prepared",
        {"port": "COM_STALE"},
        run_id=run["run_id"],
        phase="prepare",
        scope=scope,
    )
    run_dir = scope.log_root / "serial" / run["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": run["run_id"],
                "project_id": scope.project_id,
                "session_name": "stale",
                "port": "COM_STALE",
                "state": "RUNNING",
                "chunks": [],
            }
        ),
        encoding="utf-8",
    )

    first_recovery = recover_serial_runs(scope.log_root)
    first_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retry_recovery = recover_serial_runs(scope.log_root)

    assert len(first_recovery) == 1
    assert first_manifest["state"] == "FAILED"
    assert first_manifest["last_error"]["error_kind"] == "stale_monitor_recovered"
    assert first_manifest["sqlite_reconciled"] is False
    assert len(retry_recovery) == 1
    assert retry_recovery[0]["sqlite_reconciled"] is False

    binding = serial_tools.MonitorBinding(
        run_id="monitor_recovery_probe",
        project_id=scope.project_id,
        project_dir=scope.project_dir,
        log_root=scope.log_root,
        session_name="recovery-probe",
        port="COM_PROBE",
        port_identity={"port": "COM_PROBE", "device_path": "COM_PROBE"},
        baudrate=115200,
    )
    serial_tools.SERIAL_MONITOR_MANAGER._reconcile_recovered_runs(binding, retry_recovery)

    reconciled_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stored_run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    )
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        tail=10,
    )

    assert stored_run is not None
    assert stored_run["status"] == "failed"
    assert stored_run["ended_at"] is not None
    assert reconciled_manifest["sqlite_reconciled"] is True
    assert reconciled_manifest["sqlite_reconciled_at"]
    assert recover_serial_runs(scope.log_root) == []
    assert events[-1]["phase"] == "complete"
    assert events[-1]["source"] == "monitor_recovery"

def test_native_running_run_rejects_novel_jsonl_event_without_any_mutation():
    scope = LogScope.active()
    run = start_run("native_owner", run_id="native-owner-run", selected_port=None)
    native_event_uuid = str(uuid4())
    native_event = write_event(
        "native_owner",
        "info",
        "native event",
        {"value": 1},
        run_id=run["run_id"],
        ts="2026-07-20T00:00:00Z",
        phase="execute",
        event_uuid=native_event_uuid,
        source="pytest",
    )
    assert native_event.get("ok") is not False

    strict_dedup = import_legacy_jsonl()
    before_run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    )
    before_events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        tail=10,
    )

    def marker_count() -> int:
        connection = connect(scope.database_file)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM legacy_jsonl_imports WHERE project_id = ?",
                (scope.project_id,),
            ).fetchone()[0]
        finally:
            connection.close()

    before_markers = marker_count()
    assert strict_dedup["events_imported"] == 0
    assert strict_dedup["events_deduplicated"] == 1
    assert before_run is not None
    assert len(before_events) == 1
    assert before_events[0]["event_uuid"] == native_event_uuid
    assert before_markers == 1

    session = logs_dir() / "sessions" / f"{run['run_id']}.jsonl"
    existing_record = json.loads(session.read_text(encoding="utf-8").splitlines()[0])
    novel_record = {
        "event_uuid": str(uuid4()),
        "run_id": run["run_id"],
        "task_type": "native_owner",
        "selected_port": "COM_INTRUDER",
        "ts": "2026-07-20T00:00:01Z",
        "phase": "execute",
        "level": "info",
        "tool": "native_owner",
        "source": "legacy_jsonl",
        "message": "novel imported event",
        "payload_json": {"value": 2},
    }
    session.write_text(
        "\n".join(json.dumps(record) for record in (existing_record, novel_record)) + "\n",
        encoding="utf-8",
    )

    conflict = None
    try:
        import_legacy_jsonl()
    except log_repository.RunConflictError as exc:
        conflict = exc

    after_run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
    )
    after_events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run["run_id"],
        tail=10,
    )

    assert after_events == before_events
    assert after_run is not None
    assert after_run["selected_port"] == before_run["selected_port"]
    assert after_run["status"] == before_run["status"] == "running"
    assert after_run["next_sequence_no"] == before_run["next_sequence_no"]
    assert marker_count() == before_markers
    assert conflict is not None


def test_logged_task_freezes_resolved_optional_port_for_action_and_run():
    set_selected_port("COM_OPTIONAL", "sqlite contract")
    observed_ports: list[str | None] = []

    @logged_task(task_type="optional_port_binding", selected_port_arg="port")
    def optional_port_action(port: str | None = None) -> dict:
        observed_ports.append(port)
        return {"ok": True, "message": "optional port action completed", "port": port}

    result = optional_port_action()
    scope = LogScope.active()
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=result["run_id"],
    )

    assert observed_ports == ["COM_OPTIONAL"]
    assert result["ok"] is True
    assert result["port"] == "COM_OPTIONAL"
    assert run is not None
    assert run["selected_port"] == "COM_OPTIONAL"


def test_logged_task_does_not_fill_global_default_for_missing_required_port():
    set_selected_port("COM_GLOBAL_DEFAULT", "sqlite contract")
    scope = LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    business_calls: list[str] = []

    @logged_task(task_type="required_port_binding", selected_port_arg="port")
    def required_port_action(port: str) -> dict:
        business_calls.append(port)
        return {"ok": True, "port": port}

    with pytest.raises(TypeError):
        required_port_action()

    connection = connect(scope.database_file)
    try:
        required_runs = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE project_id = ? AND task_type = ?",
            (scope.project_id, "required_port_binding"),
        ).fetchone()[0]
    finally:
        connection.close()

    assert business_calls == []
    assert required_runs == 0
