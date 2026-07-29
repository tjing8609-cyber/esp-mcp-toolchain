from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4

from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.migrations import init_database
from esp_mcp_toolchain.tools import error_tools, log_tools


CREATED_AT = "2026-07-29T00:00:00+00:00"


def _create_run(scope: log_tools.LogScope, run_id: str) -> None:
    log_repository.create_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        task_type="error_parse_db_first",
        selected_port=None,
        started_at=CREATED_AT,
        summary=None,
        payload={},
    )


def _append_event(
    scope: log_tools.LogScope,
    run_id: str,
    *,
    message: str,
    payload: dict,
) -> None:
    log_repository.append_event(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        event_uuid=str(uuid4()),
        ts="2026-07-29T00:00:01+00:00",
        phase="complete",
        level="error",
        tool="error_parse_db_first",
        source="pytest",
        message=message,
        payload=payload,
    )


def _register_error(
    scope: log_tools.LogScope,
    run_id: str,
    *,
    exception_type: str,
    message: str,
) -> dict:
    record, inserted = log_repository.register_error(
        scope.database_file,
        project_id=scope.project_id,
        error_id=str(uuid4()),
        run_id=run_id,
        error_kind="micropython_traceback",
        file="main.py",
        line=9,
        column=None,
        exception_type=exception_type,
        message=message,
        raw_text=f"{exception_type}: {message}",
        recoverable=False,
        created_at="2026-07-29T00:00:02+00:00",
    )
    assert inserted is True
    return record


def _register_raw(
    scope: log_tools.LogScope,
    run_id: str,
    *,
    relative_path: str,
    sha256: str | None,
) -> dict:
    record, inserted = log_repository.register_raw_log(
        scope.database_file,
        project_id=scope.project_id,
        raw_log_id=str(uuid4()),
        run_id=run_id,
        kind="serial_capture_raw",
        path=relative_path,
        created_at="2026-07-29T00:00:02+00:00",
        sha256=sha256,
    )
    assert inserted is True
    return record


def test_v3_parse_prefers_formal_error_without_opening_raw_or_legacy_event():
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "formal-error-first"
    _create_run(scope, run_id)
    legacy_report = {
        "has_error": True,
        "error_kind": "micropython_traceback",
        "file": "legacy.py",
        "line": 3,
        "exception_type": "LegacyFault",
        "message": "legacy event must not win",
        "recoverable": True,
    }
    _append_event(
        scope,
        run_id,
        message="LegacyFault: legacy event must not win",
        payload={"has_error": True, "error_report": legacy_report},
    )
    _register_raw(
        scope,
        run_id,
        relative_path="raw/missing-must-not-open.bin",
        sha256="a" * 64,
    )
    formal = _register_error(
        scope,
        run_id,
        exception_type="DatabaseFault",
        message="formal error wins",
    )

    result = error_tools.esp_error_parse_log(run_id)

    assert result["ok"] is True
    assert result["has_error"] is True
    assert result["exception_type"] == "DatabaseFault"
    assert result["message"] == "formal error wins"
    assert result["recoverable"] is False
    assert result["scanned_bytes"] == 0
    assert result["scan_truncated"] is False
    assert result["scan_sources"] == [
        {
            "kind": "sqlite_errors",
            "count": 1,
            "error_id": formal["error_id"],
            "bytes": 0,
        }
    ]


def test_parse_log_captures_active_scope_exactly_once(monkeypatch):
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "one-scope"
    _create_run(scope, run_id)
    calls = 0

    def active_once(cls):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("esp_error_parse_log recaptured the active project")
        return scope

    monkeypatch.setattr(log_tools.LogScope, "active", classmethod(active_once))

    result = error_tools.esp_error_parse_log(run_id)

    assert result["ok"] is True
    assert result["has_error"] is False
    assert calls == 1


def test_v3_parse_uses_registered_raw_before_legacy_event_text():
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "formal-raw-first"
    _create_run(scope, run_id)
    _append_event(
        scope,
        run_id,
        message=(
            "Traceback (most recent call last):\n"
            '  File "legacy.py", line 1\n'
            "LegacyFault: legacy event must not win"
        ),
        payload={},
    )
    payload = (
        b"Traceback (most recent call last):\n"
        b'  File "formal.py", line 12\n'
        b"RawArtifactFault: registered raw wins\n"
    )
    raw_path = scope.log_root / "raw" / "formal.bin"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(payload)
    registered = _register_raw(
        scope,
        run_id,
        relative_path="raw/formal.bin",
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    result = error_tools.esp_error_parse_log(run_id, max_bytes=4096)

    assert result["ok"] is True
    assert result["has_error"] is True
    assert result["exception_type"] == "RawArtifactFault"
    assert result["file"] == "formal.py"
    assert result["scanned_bytes"] == len(payload)
    assert result["scan_sources"] == [
        {
            "kind": "sqlite_raw_log",
            "raw_log_id": registered["raw_log_id"],
            "artifact_kind": "serial_capture_raw",
            "path": "raw/formal.bin",
            "bytes": len(payload),
            "sha256_verified": True,
        }
    ]


def test_v3_parse_verifies_full_sha_beyond_scan_window():
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "tampered-after-window"
    _create_run(scope, run_id)
    original = b"x" * 5000 + b"trusted-tail"
    raw_path = scope.log_root / "raw" / "tampered.bin"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(original)
    _register_raw(
        scope,
        run_id,
        relative_path="raw/tampered.bin",
        sha256=hashlib.sha256(original).hexdigest(),
    )
    raw_path.write_bytes(b"x" * 5000 + b"tampered-tail")

    result = error_tools.esp_error_parse_log(run_id, max_bytes=4096)

    assert result["ok"] is False
    assert result["error_kind"] == "log_artifact_invalid"
    assert result["recoverable"] is False
    assert result["run_id"] == run_id
    assert result["raw_log_path"] == "raw/tampered.bin"
    assert "sha256" in result["message"].lower()


def test_v2_parse_uses_bounded_structured_event_without_migration():
    scope = log_tools.LogScope.active()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "v2-compatibility"
    _create_run(scope, run_id)
    report = {
        "has_error": True,
        "error_kind": "micropython_traceback",
        "file": "compat.py",
        "line": 5,
        "exception_type": "CompatibilityFault",
        "message": "v2 report",
        "recoverable": True,
    }
    _append_event(
        scope,
        run_id,
        message="compatibility event",
        payload={"has_error": True, "error_report": report},
    )
    connection = sqlite3.connect(scope.database_file)
    try:
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    result = error_tools.esp_error_parse_log(run_id, max_bytes=4096)

    connection = sqlite3.connect(
        f"file:{scope.database_file.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()

    assert result["ok"] is True
    assert result["has_error"] is True
    assert result["exception_type"] == "CompatibilityFault"
    assert result["query_source"] == {
        "kind": "sqlite",
        "schema_version": 2,
        "authoritative": True,
    }
    assert result["scan_sources"][-1] == {
        "kind": "structured_error_report",
        "count": 1,
        "bytes": 0,
        "compatibility": True,
    }
    assert result["scanned_bytes"] <= 4096
    assert version == 2
