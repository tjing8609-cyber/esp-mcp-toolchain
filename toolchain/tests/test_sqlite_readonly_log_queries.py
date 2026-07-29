from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from esp_mcp_toolchain.database import log_repository
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
    assert _project_snapshot(scope.project_dir) == before
    assert not scope.log_root.exists()


def test_corrupt_database_returns_structured_query_error_without_fallback():
    scope = log_tools.LogScope.active()
    scope.database_file.parent.mkdir(parents=True, exist_ok=True)
    scope.database_file.write_bytes(b"not a sqlite database")
    before = _project_snapshot(scope.project_dir)

    result = log_tools.esp_logs_get("corrupt-run")

    assert result["ok"] is False
    assert result["error_kind"] == "log_database_invalid"
    assert result["recoverable"] is False
    assert _project_snapshot(scope.project_dir) == before
    assert not scope.log_root.exists()
