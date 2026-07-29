from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from esp_mcp_toolchain.backends import (
    historical_capture_adapter,
    serial_monitor_store,
)
from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.migrations import init_database
from esp_mcp_toolchain.tools.log_tools import LogScope


RECONCILER_MODULE = (
    "esp_mcp_toolchain.backends.historical_artifact_reconciler"
)
STORE_MODULE = (
    "esp_mcp_toolchain.backends.historical_reconciliation_store"
)
EVENT_AT = "2026-07-28T08:00:00+00:00"
TERMINAL_AT = "2026-07-28T08:00:00.900Z"


def _reconciler():
    try:
        return importlib.import_module(RECONCILER_MODULE)
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"B4.4 project reconciler is missing: {exc}",
            pytrace=False,
        )


def _store():
    try:
        return importlib.import_module(STORE_MODULE)
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"B4.4 project reconciliation store is missing: {exc}",
            pytrace=False,
        )


def _scope() -> LogScope:
    return LogScope.active()


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            payload = path.read_bytes()
            snapshot[relative] = (
                len(payload),
                path.stat().st_mtime_ns,
                hashlib.sha256(payload).hexdigest(),
            )
    return snapshot


def _append_event(
    scope: LogScope,
    *,
    run_id: str,
    event_uuid: str,
    ts: str,
    phase: str,
    level: str,
    tool: str,
    source: str,
    message: str,
    payload: dict,
) -> dict:
    event, inserted = log_repository.append_event(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        event_uuid=event_uuid,
        ts=ts,
        phase=phase,
        level=level,
        tool=tool,
        source=source,
        message=message,
        payload=payload,
    )
    assert inserted is True
    return event


def _prepare_native_capture(
    scope: LogScope,
    *,
    run_id: str,
    raw_name: str,
    raw_bytes: bytes,
) -> tuple[str, Path, Path]:
    event_uuid = str(uuid4())
    prepare_uuid = str(uuid4())
    prepare_payload = {
        "baudrate": 115200,
        "duration_ms": 20,
        "session_name": "historical-native",
        "stop_on_traceback": True,
    }
    complete_payload = {
        "port": "COM3",
        "baudrate": 115200,
        "raw_path": f"Z:\\moved-project\\logs\\raw\\{raw_name}",
        "bytes_read": len(raw_bytes),
    }
    records = [
        {
            "event_uuid": prepare_uuid,
            "event_id": prepare_uuid,
            "project_id": scope.project_id,
            "run_id": run_id,
            "sequence_no": 1,
            "ts": "2026-07-28T07:59:59+00:00",
            "phase": "prepare",
            "level": "info",
            "tool": "esp_serial_capture",
            "source": "toolchain",
            "message": "esp_serial_capture started.",
            "payload_json": prepare_payload,
            "data": prepare_payload,
            "deduplicated": False,
            "task_type": "serial_capture",
            "selected_port": "COM3",
        },
        {
            "event_uuid": event_uuid,
            "event_id": event_uuid,
            "project_id": scope.project_id,
            "run_id": run_id,
            "sequence_no": 2,
            "ts": EVENT_AT,
            "phase": "complete",
            "level": "info",
            "tool": "esp_serial_capture",
            "source": "toolchain",
            "message": f"Captured {len(raw_bytes)} characters from COM3.",
            "payload_json": complete_payload,
            "data": complete_payload,
            "deduplicated": False,
            "task_type": "serial_capture",
            "selected_port": "COM3",
        },
    ]
    sessions = scope.log_root / "sessions"
    raw_root = scope.log_root / "raw"
    sessions.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    source_path = sessions / f"{run_id}.jsonl"
    source_path.write_text(
        "\n".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True)
            for record in records
        )
        + "\n",
        encoding="utf-8",
    )
    raw_path = raw_root / raw_name
    raw_path.write_bytes(raw_bytes)

    log_repository.create_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        task_type="serial_capture",
        started_at=records[0]["ts"],
        selected_port="COM3",
    )
    for record in records:
        _append_event(
            scope,
            run_id=run_id,
            event_uuid=record["event_uuid"],
            ts=record["ts"],
            phase=record["phase"],
            level=record["level"],
            tool=record["tool"],
            source=record["source"],
            message=record["message"],
            payload=record["payload_json"],
        )
    log_repository.finish_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        status="succeeded",
        ended_at=EVENT_AT,
        summary=records[-1]["message"],
    )
    return event_uuid, source_path, raw_path


def _monitor_message(state: str) -> str:
    if state == "STOPPED":
        return "Serial monitor stopped."
    if state == "DISCONNECTED":
        return "Serial monitor disconnected."
    return "Serial monitor failed."


def _prepare_monitor(
    scope: LogScope,
    *,
    run_id: str,
    state: str = "STOPPED",
    raw_bytes: bytes = b"historical monitor bytes",
    last_error: dict | None = None,
) -> tuple[str, Path]:
    run_dir = scope.log_root / "serial" / run_id
    run_dir.mkdir(parents=True)
    chunk_path = run_dir / "chunk-000001.bin"
    chunks = []
    if raw_bytes:
        chunk_path.write_bytes(raw_bytes)
        chunks.append(
            {
                "chunk_id": 1,
                "name": chunk_path.name,
                "byte_length": len(raw_bytes),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            }
        )
    manifest = {
        "format_version": 2,
        "run_id": run_id,
        "project_id": scope.project_id,
        "session_name": "historical-monitor",
        "port": "COM_HISTORY",
        "baudrate": 115200,
        "state": state,
        "process_owner": {
            "pid": 1,
            "process_token": "historical-owner-token",
            "process_started": "historical-owner-start",
        },
        "records_path": f"serial/{run_id}/records.jsonl",
        "chunks": chunks,
        "persisted_bytes": len(raw_bytes),
        "stopped_at": TERMINAL_AT,
        "last_error": last_error,
        "detected_error": None,
        "sqlite_reconciled": False,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    event_uuid = str(uuid4())
    status = "cancelled" if state == "STOPPED" else "failed"
    message = _monitor_message(state)
    log_repository.create_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        task_type="serial_monitor",
        started_at="2026-07-28T07:59:00+00:00",
        selected_port="COM_HISTORY",
    )
    _append_event(
        scope,
        run_id=run_id,
        event_uuid=event_uuid,
        ts=EVENT_AT,
        phase="complete",
        level="info" if state == "STOPPED" else "error",
        tool="esp_serial_monitor",
        source="esp32",
        message=message,
        payload={"state": state, "last_error": last_error},
    )
    log_repository.finish_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        status=status,
        ended_at=EVENT_AT,
        summary=message,
    )
    return event_uuid, manifest_path


def _claims(scope: LogScope, run_id: str) -> list[dict]:
    return log_repository.get_run_historical_raw_claims(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )


def test_status_without_control_files_has_zero_side_effects():
    scope = _scope()
    before = _tree_snapshot(scope.project_dir)

    status = _reconciler().read_historical_project_reconciliation_status(
        scope
    )

    assert status["ok"] is True
    assert status["active"] is False
    assert status["marker"] is None
    assert status["effective_state"] == "idle"
    assert _tree_snapshot(scope.project_dir) == before


def test_project_lease_is_nonblocking_persistent_and_status_observable():
    scope = _scope()
    store = _store()
    first = store.HistoricalProjectReconciliationLease.acquire(scope)
    try:
        assert first.held is True
        assert first.path.name == ".sqlite-historical-artifacts.lock"
        with pytest.raises(store.HistoricalProjectReconciliationBusy):
            store.HistoricalProjectReconciliationLease.acquire(scope)
        status = _reconciler().read_historical_project_reconciliation_status(
            scope
        )
        assert status["active"] is True
        assert status["effective_state"] == "active"
    finally:
        first.release()

    assert first.held is False
    assert first.path.exists()
    second = store.HistoricalProjectReconciliationLease.acquire(scope)
    second.release()


def test_project_lease_busy_report_is_retryable():
    scope = _scope()
    init_database(scope.database_file, project_id=scope.project_id)
    store = _store()
    lease = store.HistoricalProjectReconciliationLease.acquire(scope)
    try:
        report = _reconciler().reconcile_historical_project_artifacts(scope)
    finally:
        lease.release()

    assert report["ok"] is False
    assert report["error_kind"] == "historical_project_reconciliation_busy"
    assert report["retryable"] is True
    assert report["database_persisted"] is False
    assert report["marker_persisted"] is False


def test_status_surfaces_invalid_released_lock_metadata():
    scope = _scope()
    store = _store()
    lease = store.HistoricalProjectReconciliationLease.acquire(scope)
    lock_path = lease.path
    lease.release()
    lock_path.write_bytes(b"not-json")

    status = _reconciler().read_historical_project_reconciliation_status(
        scope
    )

    assert status["ok"] is False
    assert status["active"] is False
    assert status["active_error"] is None
    assert "invalid" in status["metadata_error"].lower()
    assert status["effective_state"] == "idle"


def test_schema_v2_is_refused_without_lock_marker_or_migration():
    scope = _scope()
    connection = sqlite3.connect(scope.database_file)
    try:
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    finally:
        connection.close()
    before = _tree_snapshot(scope.project_dir)

    report = _reconciler().reconcile_historical_project_artifacts(scope)

    assert report["ok"] is False
    assert report["error_kind"] == "historical_schema_not_ready"
    assert report["database_schema_version"] == 2
    assert report["database_persisted"] is False
    assert report["marker_persisted"] is False
    assert _tree_snapshot(scope.project_dir) == before


def test_raw_ambiguity_treats_same_event_uuid_in_different_runs_as_two_owners():
    reconciler = _reconciler()
    shared_event_uuid = str(uuid4())
    raw = log_repository.RawLogArtifact(
        kind="serial_capture_raw",
        path="raw/shared-history.log",
        sha256=hashlib.sha256(b"shared").hexdigest(),
    )
    candidate = SimpleNamespace(
        artifacts=log_repository.EventArtifacts(raw_logs=(raw,))
    )
    first = reconciler._ResolvedCandidate(
        kind="capture",
        run_id="capture-owner-a",
        event_uuid=shared_event_uuid,
        identity="sessions/capture-owner-a.jsonl",
        candidate=candidate,
        fingerprint="a" * 64,
    )
    second = reconciler._ResolvedCandidate(
        kind="capture",
        run_id="capture-owner-b",
        event_uuid=shared_event_uuid,
        identity="sessions/capture-owner-b.jsonl",
        candidate=candidate,
        fingerprint="b" * 64,
    )

    ambiguity = reconciler._raw_ambiguity([first, second])

    assert list(ambiguity) == ["raw/shared-history.log"]
    assert {
        (owner["run_id"], owner["event_uuid"])
        for owner in ambiguity["raw/shared-history.log"]
    } == {
        ("capture-owner-a", shared_event_uuid),
        ("capture-owner-b", shared_event_uuid),
    }


def test_mixed_project_reconciliation_and_exact_retry_are_idempotent():
    scope = _scope()
    init_database(scope.database_file, project_id=scope.project_id)
    capture_run = "serial_capture_project_history"
    capture_event, _source, _raw = _prepare_native_capture(
        scope,
        run_id=capture_run,
        raw_name="project_20260728_120000_012345abcdef.log",
        raw_bytes=b"\xffcapture\x00",
    )
    monitor_run = "monitor_project_history"
    monitor_event, _manifest = _prepare_monitor(
        scope,
        run_id=monitor_run,
    )

    first = _reconciler().reconcile_historical_project_artifacts(scope)
    first_raw = {
        capture_run: log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=capture_run,
        ),
        monitor_run: log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=monitor_run,
        ),
    }
    retry = _reconciler().reconcile_historical_project_artifacts(scope)

    assert first["ok"] is True
    assert first["state"] == "completed"
    assert first["counts"]["reconciled"] == 2
    assert first["database_persisted"] is True
    assert first["marker_persisted"] is True
    assert retry["ok"] is True
    assert retry["counts"]["reconciled"] == 0
    assert retry["counts"]["already_reconciled"] == 2
    assert len(_claims(scope, capture_run)) == 1
    assert len(_claims(scope, monitor_run)) == 1
    assert _claims(scope, capture_run)[0]["event_uuid"] == capture_event
    assert _claims(scope, monitor_run)[0]["event_uuid"] == monitor_event
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=capture_run,
    ) == first_raw[capture_run]
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=monitor_run,
    ) == first_raw[monitor_run]
    marker = _store().load_historical_reconciliation_marker(scope)
    assert marker is not None
    assert marker["state"] == "completed"
    assert marker["project_id"] == scope.project_id
    assert not (
        scope.log_root
        / "serial"
        / monitor_run
        / serial_monitor_store.SQLITE_ARTIFACT_MARKER_NAME
    ).exists()


def test_capture_raw_ambiguity_blocks_every_database_write():
    scope = _scope()
    init_database(scope.database_file, project_id=scope.project_id)
    shared_name = "shared_20260728_120000_012345abcdef.log"
    first_run = "serial_capture_ambiguous_first"
    second_run = "serial_capture_ambiguous_second"
    _prepare_native_capture(
        scope,
        run_id=first_run,
        raw_name=shared_name,
        raw_bytes=b"shared bytes",
    )
    _prepare_native_capture(
        scope,
        run_id=second_run,
        raw_name=shared_name,
        raw_bytes=b"shared bytes",
    )

    report = _reconciler().reconcile_historical_project_artifacts(scope)

    assert report["ok"] is False
    assert report["error_kind"] == "historical_raw_path_ambiguous"
    assert report["database_persisted"] is False
    assert _claims(scope, first_run) == []
    assert _claims(scope, second_run) == []
    for run_id in (first_run, second_run):
        assert log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        ) == []


def test_second_resolution_change_fails_before_database_write(monkeypatch):
    scope = _scope()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "serial_capture_changed_between_passes"
    _event_uuid, _source, raw_path = _prepare_native_capture(
        scope,
        run_id=run_id,
        raw_name="changed_20260728_120000_012345abcdef.log",
        raw_bytes=b"original",
    )
    original = (
        historical_capture_adapter
        .resolve_historical_serial_capture_artifacts
    )
    calls = 0

    def change_after_first(*args, **kwargs):
        nonlocal calls
        candidate = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            raw_path.write_bytes(b"modified")
        return candidate

    monkeypatch.setattr(
        historical_capture_adapter,
        "resolve_historical_serial_capture_artifacts",
        change_after_first,
    )

    report = _reconciler().reconcile_historical_project_artifacts(scope)

    assert calls == 2
    assert report["ok"] is False
    assert report["error_kind"] == "historical_candidate_changed"
    assert report["database_persisted"] is False
    assert _claims(scope, run_id) == []


def test_monitor_run_busy_aborts_before_database_and_releases_project_lease():
    scope = _scope()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "monitor_history_busy"
    _prepare_monitor(scope, run_id=run_id)
    run_dir = scope.log_root / "serial" / run_id
    run_lease = serial_monitor_store.SerialRunReconciliationLease.acquire(
        run_dir
    )
    try:
        report = _reconciler().reconcile_historical_project_artifacts(scope)
    finally:
        run_lease.release()

    assert report["ok"] is False
    assert report["error_kind"] == "historical_monitor_busy"
    assert report["database_persisted"] is False
    assert _claims(scope, run_id) == []
    project_lease = (
        _store().HistoricalProjectReconciliationLease.acquire(scope)
    )
    project_lease.release()


def test_database_commit_survives_final_marker_failure_and_retry_repairs_it(
    monkeypatch,
):
    scope = _scope()
    init_database(scope.database_file, project_id=scope.project_id)
    run_id = "serial_capture_marker_failure"
    _prepare_native_capture(
        scope,
        run_id=run_id,
        raw_name="marker_20260728_120000_012345abcdef.log",
        raw_bytes=b"marker failure",
    )
    store = _store()
    original_publish = store.publish_historical_reconciliation_marker

    def fail_completed_marker(scope_arg, lease, document):
        if document.get("state") == "completed":
            raise store.HistoricalProjectReconciliationStoreError(
                "forced final marker failure"
            )
        return original_publish(scope_arg, lease, document)

    monkeypatch.setattr(
        store,
        "publish_historical_reconciliation_marker",
        fail_completed_marker,
    )
    failed = _reconciler().reconcile_historical_project_artifacts(scope)

    assert failed["ok"] is False
    assert failed["error_kind"] == "historical_marker_publish_failed"
    assert failed["database_persisted"] is True
    assert failed["marker_persisted"] is False
    assert len(_claims(scope, run_id)) == 1

    monkeypatch.setattr(
        store,
        "publish_historical_reconciliation_marker",
        original_publish,
    )
    repaired = _reconciler().reconcile_historical_project_artifacts(scope)
    assert repaired["ok"] is True
    assert repaired["counts"]["already_reconciled"] == 1
    assert repaired["marker_persisted"] is True
    assert len(_claims(scope, run_id)) == 1


def test_released_running_marker_is_reported_as_interrupted():
    scope = _scope()
    store = _store()
    lease = store.HistoricalProjectReconciliationLease.acquire(scope)
    try:
        store.publish_historical_reconciliation_marker(
            scope,
            lease,
            {
                "format": "esp-mcp-toolchain.historical-sqlite-artifacts",
                "version": 1,
                "project_id": scope.project_id,
                "state": "running",
                "started_at": EVENT_AT,
                "completed_at": None,
                "database_persisted": False,
                "counts": {},
                "items": [],
                "error": None,
            },
        )
    finally:
        lease.release()

    status = _reconciler().read_historical_project_reconciliation_status(
        scope
    )
    assert status["active"] is False
    assert status["marker"]["state"] == "running"
    assert status["effective_state"] == "interrupted"
