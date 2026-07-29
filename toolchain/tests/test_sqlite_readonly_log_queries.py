from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.db import connect_readonly
from esp_mcp_toolchain.database.migrations import init_database
from esp_mcp_toolchain.tools import log_tools


def _project_snapshot(project_dir: Path) -> dict[str, tuple[int, int, str]]:
    if not project_dir.exists():
        return {}
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


def _create_v2_query_fixture() -> tuple[log_tools.LogScope, str]:
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "readonly-v2-run"
    log_repository.create_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        task_type="readonly_query",
        selected_port=None,
        started_at="2026-07-29T00:00:00+00:00",
        summary=None,
        payload={},
    )
    log_repository.append_event(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        event_uuid=str(uuid4()),
        ts="2026-07-29T00:00:01+00:00",
        phase="complete",
        level="info",
        tool="readonly_query",
        source="pytest",
        message="v2 query evidence",
        payload={},
    )
    connection = sqlite3.connect(scope.database_file)
    try:
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    return scope, run_id


def test_missing_database_queries_have_zero_filesystem_side_effects():
    scope = log_tools.LogScope.active()
    before = _project_snapshot(scope.project_dir)

    latest = log_tools.esp_logs_latest()
    missing = log_tools.esp_logs_get("missing-run")
    queried = log_tools.esp_logs_query("missing", limit=5)

    assert latest == {"ok": True, "latest": None}
    assert missing["ok"] is False
    assert missing["error_kind"] == "run_not_found"
    assert queried["ok"] is True
    assert queried["matches"] == []
    assert _project_snapshot(scope.project_dir) == before
    assert not scope.database_file.exists()
    assert not scope.log_root.exists()


def test_queries_do_not_import_jsonl_when_database_is_missing():
    scope = log_tools.LogScope.active()
    session = scope.log_root / "sessions" / "jsonl-bait.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "event_uuid": str(uuid4()),
                "run_id": "jsonl-bait",
                "task_type": "legacy",
                "ts": "2026-07-29T00:00:00+00:00",
                "phase": "complete",
                "level": "error",
                "tool": "legacy",
                "source": "legacy_jsonl",
                "message": "must remain an audit mirror",
                "payload_json": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    before = _project_snapshot(scope.project_dir)

    result = log_tools.esp_logs_get("jsonl-bait")

    assert result["ok"] is False
    assert result["error_kind"] == "run_not_found"
    assert _project_snapshot(scope.project_dir) == before
    assert not scope.database_file.exists()


def test_v2_queries_are_read_only_and_do_not_upgrade_schema():
    scope, run_id = _create_v2_query_fixture()
    before = _project_snapshot(scope.project_dir)

    result = log_tools.esp_logs_get(run_id)
    latest = log_tools.esp_logs_latest()
    queried = log_tools.esp_logs_query("v2 query evidence", run_id=run_id)

    connection = sqlite3.connect(f"file:{scope.database_file.as_posix()}?mode=ro", uri=True)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()

    assert result["ok"] is True
    assert [event["message"] for event in result["events"]] == ["v2 query evidence"]
    assert latest["latest"]["run_id"] == run_id
    assert [event["run_id"] for event in queried["matches"]] == [run_id]
    assert version == 2
    after = _project_snapshot(scope.project_dir)
    sqlite_coordination_files = {
        f"{scope.database_file.name}-wal",
        f"{scope.database_file.name}-shm",
    }
    assert {
        path: metadata
        for path, metadata in after.items()
        if path not in sqlite_coordination_files
    } == {
        path: metadata
        for path, metadata in before.items()
        if path not in sqlite_coordination_files
    }
    assert not scope.log_root.exists()


def test_corrupt_database_returns_structured_query_error_without_fallback():
    scope = log_tools.LogScope.active()
    scope.database_file.parent.mkdir(parents=True, exist_ok=True)
    scope.database_file.write_bytes(b"not a sqlite database")
    before = _project_snapshot(scope.project_dir)

    results = [
        log_tools.esp_logs_latest(),
        log_tools.esp_logs_get("corrupt-run"),
        log_tools.esp_logs_query("corrupt"),
    ]

    assert all(result["ok"] is False for result in results)
    assert all(result["error_kind"] == "log_database_invalid" for result in results)
    assert all(result["recoverable"] is False for result in results)
    assert _project_snapshot(scope.project_dir) == before
    assert not scope.log_root.exists()


def test_readonly_connection_enforces_query_only():
    scope, _run_id = _create_v2_query_fixture()
    before = _project_snapshot(scope.project_dir)[scope.database_file.name]

    connection = connect_readonly(scope.database_file)
    try:
        assert int(connection.execute("PRAGMA query_only").fetchone()[0]) == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden_write(value TEXT)")
    finally:
        connection.close()

    assert _project_snapshot(scope.project_dir)[scope.database_file.name] == before


def test_invalid_arguments_are_rejected_before_database_open(monkeypatch):
    def fail_if_opened(*_args, **_kwargs):
        raise AssertionError("database query must not run for invalid arguments")

    monkeypatch.setattr(log_repository, "read_run_snapshot", fail_if_opened)
    monkeypatch.setattr(log_repository, "query_events_readonly", fail_if_opened)

    assert log_tools.esp_logs_get("run", tail=0)["error_kind"] == "invalid_tail"
    assert log_tools.esp_logs_query(limit=0)["error_kind"] == "invalid_limit"
    assert (
        log_tools.esp_logs_query(sequence_from=2, sequence_to=1, run_id="run")[
            "error_kind"
        ]
        == "invalid_sequence_range"
    )


def test_invalid_stored_payload_returns_structured_database_error():
    scope, run_id = _create_v2_query_fixture()
    connection = sqlite3.connect(scope.database_file)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE runs SET payload_json = 'not-json' "
            "WHERE project_id = ? AND run_id = ?",
            (scope.project_id, run_id),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    result = log_tools.esp_logs_get(run_id)

    assert result["ok"] is False
    assert result["error_kind"] == "log_database_invalid"
    assert result["recoverable"] is False


def test_unknown_or_incomplete_schema_is_rejected_without_migration():
    scope = log_tools.LogScope.active()
    scope.database_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(scope.database_file)
    try:
        connection.execute("CREATE TABLE runs(run_id TEXT)")
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    finally:
        connection.close()
    before = _project_snapshot(scope.project_dir)

    result = log_tools.esp_logs_get("run")

    assert result["ok"] is False
    assert result["error_kind"] == "log_database_schema_unsupported"
    assert result["recoverable"] is False
    assert _project_snapshot(scope.project_dir) == before
    assert not scope.log_root.exists()


def test_locked_database_is_unavailable_not_corrupt(monkeypatch):
    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(log_repository, "connect_readonly", locked)

    results = [
        log_tools.esp_logs_latest(),
        log_tools.esp_logs_get("locked-run"),
        log_tools.esp_logs_query("locked"),
    ]

    assert all(result["ok"] is False for result in results)
    assert all(result["error_kind"] == "log_database_unavailable" for result in results)
    assert all(result["recoverable"] is True for result in results)
