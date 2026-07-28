from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import threading
import time
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest

from esp_mcp_toolchain.backends import serial_monitor_backend, serial_monitor_store
from esp_mcp_toolchain.backends.serial_monitor_backend import (
    MonitorBinding,
    SerialMonitorManager,
)
from esp_mcp_toolchain.backends.serial_monitor_store import (
    SQLITE_ARTIFACT_RECONCILIATION_VERSION,
    SerialLogReconciliationBusy,
    SerialLogStoreError,
    SerialRunReconciliationLease,
    read_persisted_records,
    recover_serial_runs,
)
from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.error_repository import stable_error_id
from esp_mcp_toolchain.database.event_repository import normalize_timestamp
from esp_mcp_toolchain.database.raw_log_repository import RawLogRepositoryError
from esp_mcp_toolchain.project_context import get_project_context
from esp_mcp_toolchain.tools import serial_tools
from esp_mcp_toolchain.tools.log_tools import (
    LogScope,
    latest_path,
    session_path,
    start_run,
    write_event,
)


ARTIFACT_VERSION = SQLITE_ARTIFACT_RECONCILIATION_VERSION


class MonitorSerial:
    queue: Queue = Queue()
    instances: list["MonitorSerial"] = []

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
        self.closed = threading.Event()
        type(self).instances.append(self)

    def open(self) -> None:
        return None

    def read(self, _size: int) -> bytes:
        if self.closed.is_set():
            return b""
        try:
            return type(self).queue.get(timeout=0.02)
        except Empty:
            return b""

    def cancel_read(self) -> None:
        type(self).queue.put(b"")

    def close(self) -> None:
        self.closed.set()


class MonitorSerialModule:
    Serial = MonitorSerial


def _identity(port: str) -> dict:
    return {
        "port": port,
        "device_path": port,
        "vid": "FFFF",
        "pid": "0001",
        "serial_number": port,
        "location": "sqlite-monitor-test",
    }


@pytest.fixture(autouse=True)
def fake_monitor(monkeypatch):
    serial_tools.SERIAL_MONITOR_MANAGER.shutdown_all(1)
    MonitorSerial.queue = Queue()
    MonitorSerial.instances = []
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: MonitorSerialModule)
    monkeypatch.setattr(serial_tools, "describe_serial_port", _identity)
    yield
    serial_tools.SERIAL_MONITOR_MANAGER.shutdown_all(1)


def _wait_for_monitor(run_id: str, predicate, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = serial_tools.esp_serial_monitor_status(run_id)
        if result["monitors"] and predicate(result["monitors"][0]):
            return result["monitors"][0]
        time.sleep(0.01)
    raise AssertionError(f"Monitor {run_id} did not reach the expected state.")


def _scope() -> LogScope:
    return LogScope.active()


def _binding(scope: LogScope) -> MonitorBinding:
    return MonitorBinding(
        run_id="monitor_reconciliation_probe",
        project_id=scope.project_id,
        project_dir=scope.project_dir,
        log_root=scope.log_root,
        session_name="reconciliation-probe",
        port="COM_RECONCILE",
        port_identity=_identity("COM_RECONCILE"),
        baudrate=115200,
    )


def _terminal_manifest(
    scope: LogScope,
    *,
    run_id: str,
    state: str = "STOPPED",
    payload: bytes = b"monitor-chunk",
    path_value: str | None = None,
    byte_length: int | None = None,
    sha256: str | None = None,
    last_error: dict | None = None,
    detected_error: dict | None = None,
    task_type: str = "serial_monitor",
) -> tuple[Path, Path]:
    start_run(
        task_type,
        run_id=run_id,
        selected_port="COM_RECONCILE",
        scope=scope,
    )
    write_event(
        "esp_serial_monitor_start",
        "info",
        "monitor prepared",
        {"port": "COM_RECONCILE"},
        run_id=run_id,
        phase="prepare",
        scope=scope,
    )
    run_dir = scope.log_root / "serial" / run_id
    run_dir.mkdir(parents=True)
    chunk_path = run_dir / "chunk-000001.bin"
    chunk_path.write_bytes(payload)
    manifest = {
        "format_version": 1,
        "run_id": run_id,
        "project_id": scope.project_id,
        "session_name": "reconciliation",
        "port": "COM_RECONCILE",
        "baudrate": 115200,
        "state": state,
        "stopped_at": "2026-07-28T01:00:00+00:00",
        "last_error": last_error,
        "detected_error": detected_error,
        "chunks": [
            {
                "chunk_id": 1,
                "path": path_value if path_value is not None else str(chunk_path),
                "byte_length": len(payload) if byte_length is None else byte_length,
                "sha256": hashlib.sha256(payload).hexdigest() if sha256 is None else sha256,
            }
        ],
        "sqlite_artifacts_reconciliation_version": 0,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, chunk_path


def _manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _projection(path: Path) -> dict:
    return _artifact_marker(path)["projection"]


def _artifact_marker(path: Path) -> dict:
    return json.loads(
        path.with_name("sqlite-artifacts-v1.json").read_text(encoding="utf-8")
    )


def _reconcile(scope: LogScope, recovered: list[dict]) -> list[dict]:
    return SerialMonitorManager._reconcile_recovered_runs(_binding(scope), recovered)


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Creating a test symlink is unavailable: {exc}")


def _directory_reparse(link: Path, target: Path) -> None:
    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
        return
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, (
        f"mklink /J failed: {completed.stdout} {completed.stderr}"
    )
    attributes = getattr(link.lstat(), "st_file_attributes", 0)
    assert attributes & 0x400


def test_monitor_stop_registers_each_finalized_chunk_once(monkeypatch):
    monkeypatch.setenv("ESP_MCP_MONITOR_CHUNK_BYTES", "4")
    started = serial_tools.esp_serial_monitor_start(
        "COM_MONITOR_RAW",
        session_name="sqlite-raw",
    )
    run_id = started["run_id"]
    MonitorSerial.queue.put(b"abc")
    MonitorSerial.queue.put(b"def")
    running = _wait_for_monitor(
        run_id,
        lambda status: status["persisted_bytes"] == 6,
    )
    assert running["logging_persistence_state"] == "not_terminal"
    assert running["logging_persisted"] is None

    stopped = serial_tools.esp_serial_monitor_stop(run_id)
    scope = _scope()
    raw_logs = log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    first_ids = [record["raw_log_id"] for record in raw_logs]
    repeated = serial_tools.esp_serial_monitor_stop(run_id)

    assert stopped["ok"] is True, stopped
    assert stopped["monitor"]["logging_persistence_state"] == "committed"
    assert stopped["monitor"]["logging_persisted"] is True
    assert repeated["ok"] is True
    raw_logs = sorted(raw_logs, key=lambda record: record["path"])
    assert [record["path"] for record in raw_logs] == [
        f"serial/{run_id}/chunk-000001.bin",
        f"serial/{run_id}/chunk-000002.bin",
    ]
    assert {record["kind"] for record in raw_logs} == {"serial_monitor_chunk"}
    assert [record["sha256"] for record in raw_logs] == [
        hashlib.sha256(b"abc").hexdigest(),
        hashlib.sha256(b"def").hexdigest(),
    ]
    assert [
        record["raw_log_id"]
        for record in log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    ] == first_ids
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )
    completion = [event for event in events if event["phase"] == "complete"]
    assert len(completion) == 1
    assert {record["created_at"] for record in raw_logs} == {completion[0]["ts"]}
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )["status"] == "cancelled"
    manifest_path = scope.log_root / "serial" / run_id / "manifest.json"
    persisted = _artifact_marker(manifest_path)
    assert persisted["version"] == ARTIFACT_VERSION
    assert persisted["projection"]["state"] == "committed"
    assert persisted["audit_mirror"]["state"] == "committed"
    assert persisted["projection"]["event_uuid"]
    assert persisted["terminal_marker"]["event_uuid"] == (
        persisted["projection"]["event_uuid"]
    )
    assert "sqlite_reconciled" not in persisted


def test_clean_monitor_stop_commits_without_raw_artifacts():
    started = serial_tools.esp_serial_monitor_start(
        "COM_MONITOR_EMPTY",
        session_name="sqlite-empty",
    )
    run_id = started["run_id"]

    stopped = serial_tools.esp_serial_monitor_stop(run_id)

    scope = _scope()
    manifest_path = scope.log_root / "serial" / run_id / "manifest.json"
    manifest = _manifest(manifest_path)
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )
    assert stopped["ok"] is True
    assert "sqlite_artifact_projection" not in manifest
    assert _projection(manifest_path)["state"] == "committed"
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == []
    assert len([event for event in events if event["phase"] == "complete"]) == 1
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )["status"] == "cancelled"


def test_monitor_traceback_registers_error_for_terminal_event():
    traceback = (
        b"Traceback (most recent call last):\r\n"
        b'  File "main.py", line 7\r\n'
        b"RuntimeError: monitor boom\r\n"
    )
    started = serial_tools.esp_serial_monitor_start(
        "COM_MONITOR_ERROR",
        session_name="sqlite-error",
    )
    run_id = started["run_id"]
    MonitorSerial.queue.put(traceback)
    _wait_for_monitor(
        run_id,
        lambda status: isinstance(status.get("detected_error"), dict),
    )
    serial_tools.esp_serial_monitor_stop(run_id)

    scope = _scope()
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )
    completion = next(event for event in events if event["phase"] == "complete")
    errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )

    assert len(errors) == 1
    assert errors[0]["error_kind"] == "micropython_traceback"
    assert errors[0]["exception_type"] == "RuntimeError"
    assert errors[0]["line"] == 7
    artifact_marker = _artifact_marker(
        scope.log_root / "serial" / run_id / "manifest.json"
    )
    assert errors[0]["created_at"] == normalize_timestamp(
        artifact_marker["first_runtime_error"]["detected_at"]
    )
    assert errors[0]["error_id"] == stable_error_id(
        project_id=scope.project_id,
        run_id=run_id,
        occurrence_key=f"event:{completion['event_uuid']}:detected_error",
        error_kind=errors[0]["error_kind"],
        file=errors[0]["file"],
        line=errors[0]["line"],
        column=errors[0]["column"],
        exception_type=errors[0]["exception_type"],
        message=errors[0]["message"],
        raw_text=errors[0]["raw_text"],
    )
    assert (
        artifact_marker["terminal_marker"]["event_uuid"]
        == completion["event_uuid"]
    )
    assert artifact_marker["projection"]["state"] == "committed"


def test_stopped_crash_window_reconciles_finalized_chunk():
    scope = _scope()
    manifest_path, _chunk = _terminal_manifest(
        scope,
        run_id="monitor_stopped_artifact_gap",
    )
    stored_before = _manifest(manifest_path)
    stored_before["sqlite_reconciled"] = True
    manifest_path.write_text(json.dumps(stored_before), encoding="utf-8")

    recovered = recover_serial_runs(scope.log_root)
    _reconcile(scope, recovered)

    stored = _manifest(manifest_path)
    artifact_marker = _artifact_marker(manifest_path)
    raw_logs = log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id="monitor_stopped_artifact_gap",
    )
    assert len(recovered) == 1
    assert "sqlite_artifacts_reconciliation_version" not in stored
    assert artifact_marker["projection"]["state"] == "committed"
    assert raw_logs[0]["path"] == (
        "serial/monitor_stopped_artifact_gap/chunk-000001.bin"
    )
    assert raw_logs[0]["sha256"] == hashlib.sha256(b"monitor-chunk").hexdigest()
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id="monitor_stopped_artifact_gap",
    )["status"] == "cancelled"


def test_artifact_projection_uses_an_independent_versioned_marker():
    scope = _scope()
    run_id = "monitor_independent_artifact_marker"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    manifest = _manifest(manifest_path)
    artifact_marker = _artifact_marker(manifest_path)
    assert reports[0]["ok"] is True
    assert "terminal_marker" not in manifest
    assert "sqlite_artifact_projection" not in manifest
    assert "sqlite_artifacts_reconciliation_version" not in manifest
    assert "sqlite_artifacts_reconciliation_error" not in manifest
    assert artifact_marker["version"] == ARTIFACT_VERSION
    assert artifact_marker["project_id"] == scope.project_id
    assert artifact_marker["run_id"] == run_id
    assert artifact_marker["terminal_marker"]["event_uuid"]
    assert artifact_marker["projection"]["state"] == "committed"


def test_unknown_artifact_marker_version_is_refused_without_mutation():
    scope = _scope()
    run_id = "monitor_unknown_artifact_version"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    unknown_path = manifest_path.with_name("sqlite-artifacts-v2.json")
    original = b'{"version":2,"opaque":"leave-me-alone"}\n'
    unknown_path.write_bytes(original)

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert len(reports) == 1
    assert reports[0]["ok"] is False
    assert "unsupported" in reports[0]["message"].lower()
    assert unknown_path.read_bytes() == original
    assert not manifest_path.with_name("sqlite-artifacts-v1.json").exists()
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == []
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )["status"] == "running"


def test_forged_terminal_marker_is_refused_without_mutation():
    scope = _scope()
    run_id = "monitor_forged_terminal_marker"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    event_uuid = str(
        uuid5(
            NAMESPACE_URL,
            (
                "esp-mcp-toolchain:serial-monitor-terminal:"
                f"v1:{scope.project_id}:{run_id}"
            ),
        )
    )
    marker_path = manifest_path.with_name("sqlite-artifacts-v1.json")
    forged = {
        "format": "esp-mcp-toolchain.serial-sqlite-artifacts",
        "version": ARTIFACT_VERSION,
        "project_id": scope.project_id,
        "run_id": run_id,
        "terminal_marker": {
            "version": 1,
            "marker_id": event_uuid,
            "event_uuid": event_uuid,
            "project_id": scope.project_id,
            "run_id": run_id,
            "state": "STOPPED",
            "run_status": "cancelled",
            "terminal_at": "2026-07-28T01:00:00+00:00",
            "level": "info",
            "tool": "forged_tool",
            "source": "serial_monitor_terminal",
            "message": "Serial monitor stopped.",
            "last_error": None,
            "detected_error": None,
            "detected_error_at": None,
            "stale_recovery": False,
        },
        "first_runtime_error": None,
        "projection": {
            "state": "pending",
            "event_uuid": event_uuid,
            "completed_at": None,
            "error": None,
        },
        "audit_mirror": {
            "state": "pending",
            "event_uuid": event_uuid,
            "completed_at": None,
            "error": None,
        },
    }
    original = (json.dumps(forged, indent=2) + "\n").encode()
    marker_path.write_bytes(original)

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert len(reports) == 1
    assert reports[0]["ok"] is False
    assert "canonical" in reports[0]["message"].lower()
    assert marker_path.read_bytes() == original
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == []
    assert [
        event["phase"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=20,
        )
    ] == ["prepare"]


def test_terminal_manifest_refuses_an_unlisted_final_chunk():
    scope = _scope()
    run_id = "monitor_unlisted_terminal_chunk"
    manifest_path, first_chunk = _terminal_manifest(scope, run_id=run_id)
    injected = first_chunk.with_name("chunk-000002.bin")
    injected.write_bytes(b"must-not-be-adopted")
    manifest_before = manifest_path.read_bytes()

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert len(reports) == 1
    assert reports[0]["ok"] is False
    assert "chunk" in reports[0]["message"].lower()
    assert manifest_path.read_bytes() == manifest_before
    assert injected.read_bytes() == b"must-not-be-adopted"
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == []
    assert [
        event["phase"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=20,
        )
    ] == ["prepare"]


@pytest.mark.parametrize("path_kind", ["outside", "unc"])
def test_legacy_chunk_path_is_rejected_without_resolving_the_supplied_path(
    monkeypatch,
    tmp_path,
    path_kind,
):
    scope = _scope()
    supplied_path = (
        str(tmp_path / "outside.bin")
        if path_kind == "outside"
        else r"\\untrusted-host\share\chunk-000001.bin"
    )
    run_id = f"monitor_legacy_lexical_{path_kind}"
    manifest_path, _chunk = _terminal_manifest(
        scope,
        run_id=run_id,
        path_value=supplied_path,
    )
    original_resolve = Path.resolve
    supplied_resolve_calls: list[str] = []

    def guarded_resolve(path, *args, **kwargs):
        if str(path) == supplied_path:
            supplied_resolve_calls.append(str(path))
            raise AssertionError("supplied external path must not be resolved")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert supplied_resolve_calls == []
    assert len(reports) == 1
    assert reports[0]["ok"] is False
    assert "path" in reports[0]["message"].lower()
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == []
    assert _artifact_marker(manifest_path)["projection"]["state"] == "failed"


def test_fd_bound_chunk_verification_rejects_identity_change(monkeypatch, tmp_path):
    chunk = tmp_path / "chunk-000001.bin"
    chunk.write_bytes(b"identity-must-remain-stable")
    original_fstat = serial_monitor_store.os.fstat
    calls = 0

    def changing_fstat(descriptor):
        nonlocal calls
        calls += 1
        current = original_fstat(descriptor)
        if calls != 2:
            return current
        return SimpleNamespace(
            st_mode=current.st_mode,
            st_dev=current.st_dev,
            st_ino=current.st_ino,
            st_size=current.st_size + 1,
            st_mtime_ns=current.st_mtime_ns,
            st_file_attributes=getattr(current, "st_file_attributes", 0),
        )

    monkeypatch.setattr(serial_monitor_store.os, "fstat", changing_fstat)

    with pytest.raises(SerialLogStoreError, match="changed while"):
        serial_monitor_store._verified_file_digest(
            chunk,
            parent=tmp_path,
            label="Test monitor chunk",
        )

    assert calls == 2


def test_safe_binary_reader_transfers_descriptor_ownership_once(
    monkeypatch,
    tmp_path,
):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    original_close = serial_monitor_store.os.close
    explicit_close_attempts: list[int] = []

    def recording_close(descriptor: int) -> None:
        explicit_close_attempts.append(descriptor)
        original_close(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(serial_monitor_store.os, "close", recording_close)
        assert serial_monitor_store._read_safe_json_object(
            manifest,
            parent=tmp_path,
            label="Test monitor manifest",
        ) == {}
    assert explicit_close_attempts == []


def test_safe_binary_reader_closes_untransferred_descriptor_once(
    monkeypatch,
    tmp_path,
):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    original_open = serial_monitor_store._open_readonly_no_reparse
    original_close = serial_monitor_store.os.close
    opened_descriptors: list[int] = []
    closed_descriptors: list[int] = []

    def recording_open(path: Path) -> int:
        descriptor = original_open(path)
        opened_descriptors.append(descriptor)
        return descriptor

    def recording_close(descriptor: int) -> None:
        closed_descriptors.append(descriptor)
        original_close(descriptor)

    def fail_fdopen(*_args, **_kwargs):
        raise OSError("fdopen failed before ownership transfer")

    with monkeypatch.context() as patch:
        patch.setattr(
            serial_monitor_store,
            "_open_readonly_no_reparse",
            recording_open,
        )
        patch.setattr(serial_monitor_store.os, "close", recording_close)
        patch.setattr(serial_monitor_store.os, "fdopen", fail_fdopen)

        with pytest.raises(OSError, match="before ownership transfer"):
            serial_monitor_store._read_safe_json_object(
                manifest,
                parent=tmp_path,
                label="Test monitor manifest",
            )

    assert len(opened_descriptors) == 1
    assert closed_descriptors == opened_descriptors
    with pytest.raises(OSError):
        serial_monitor_store.os.fstat(opened_descriptors[0])


def test_first_runtime_error_is_frozen_before_monitor_cleanup():
    traceback = (
        b"Traceback (most recent call last):\r\n"
        b'  File "main.py", line 9\r\n'
        b"RuntimeError: freeze-before-cleanup\r\n"
    )
    started = serial_tools.esp_serial_monitor_start(
        "COM_MONITOR_FREEZE",
        session_name="freeze-runtime-error",
    )
    run_id = started["run_id"]
    MonitorSerial.queue.put(traceback)
    _wait_for_monitor(
        run_id,
        lambda status: isinstance(status.get("detected_error"), dict),
    )

    scope = _scope()
    manifest_path = scope.log_root / "serial" / run_id / "manifest.json"
    artifact_marker = _artifact_marker(manifest_path)
    assert artifact_marker["terminal_marker"] is None
    assert artifact_marker["projection"]["state"] == "not_terminal"
    assert artifact_marker["first_runtime_error"]["detected_at"]
    assert artifact_marker["first_runtime_error"]["report"]["line"] == 9
    assert (
        artifact_marker["first_runtime_error"]["report"]["exception_type"]
        == "RuntimeError"
    )

    stopped = serial_tools.esp_serial_monitor_stop(run_id)
    assert stopped["monitor"]["logging_persistence_state"] == "committed"
    assert _artifact_marker(manifest_path)["projection"]["state"] == "committed"


def test_stale_recovery_projects_the_frozen_first_runtime_error():
    scope = _scope()
    run_id = "monitor_frozen_runtime_recovery"
    manifest_path, _chunk = _terminal_manifest(
        scope,
        run_id=run_id,
        state="RUNNING",
    )
    detected_at = "2026-07-28T01:00:00+00:00"
    marker_path = manifest_path.with_name("sqlite-artifacts-v1.json")
    marker_path.write_text(
        json.dumps(
            {
                "format": "esp-mcp-toolchain.serial-sqlite-artifacts",
                "version": ARTIFACT_VERSION,
                "project_id": scope.project_id,
                "run_id": run_id,
                "terminal_marker": None,
                "first_runtime_error": {
                    "detected_at": detected_at,
                    "report": {
                        "has_error": True,
                        "error_kind": "micropython_traceback",
                        "file": "main.py",
                        "line": 17,
                        "exception_type": "RuntimeError",
                        "message": "crashed before cleanup",
                        "recoverable": True,
                    },
                },
                "projection": {
                    "state": "not_terminal",
                    "event_uuid": None,
                    "completed_at": None,
                    "error": None,
                },
                "audit_mirror": {
                    "state": "not_terminal",
                    "event_uuid": None,
                    "completed_at": None,
                    "error": None,
                },
            }
        ),
        encoding="utf-8",
    )

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert reports[0]["ok"] is True
    errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    runtime_error = next(
        error for error in errors if error["error_kind"] == "micropython_traceback"
    )
    assert runtime_error["created_at"] == detected_at
    assert runtime_error["line"] == 17
    artifact_marker = _artifact_marker(manifest_path)
    assert artifact_marker["terminal_marker"]["detected_error_at"] == detected_at
    assert artifact_marker["projection"]["state"] == "committed"


def test_stale_part_recovery_registers_one_chunk_error_and_completion():
    scope = _scope()
    run_id = "monitor_stale_artifacts"
    start_run(
        "serial_monitor",
        run_id=run_id,
        selected_port="COM_STALE",
        scope=scope,
    )
    write_event(
        "esp_serial_monitor_start",
        "info",
        "monitor prepared",
        {},
        run_id=run_id,
        phase="prepare",
        scope=scope,
    )
    run_dir = scope.log_root / "serial" / run_id
    run_dir.mkdir(parents=True)
    payload = b"stale-part"
    (run_dir / "chunk-000001.bin.part").write_bytes(payload)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "run_id": run_id,
                "project_id": scope.project_id,
                "session_name": "stale",
                "port": "COM_STALE",
                "state": "RUNNING",
                "chunks": [],
                "sqlite_artifacts_reconciliation_version": 0,
            }
        ),
        encoding="utf-8",
    )

    recovered = recover_serial_runs(scope.log_root)
    _reconcile(scope, recovered)
    _reconcile(scope, recovered)
    second_recovery = recover_serial_runs(scope.log_root)
    second_reports = _reconcile(scope, second_recovery)

    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )
    raw_logs = log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    completion = [event for event in events if event["phase"] == "complete"]
    stored = _manifest(manifest_path)

    assert len(second_recovery) == 1
    assert second_reports[0]["ok"] is True
    assert len(completion) == 1
    assert completion[0]["source"] == "monitor_recovery"
    assert len(raw_logs) == 1
    assert raw_logs[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert [error["error_kind"] for error in errors] == [
        "stale_monitor_recovered"
    ]
    assert stored["sqlite_reconciled"] is True
    assert "sqlite_artifacts_reconciliation_version" not in stored
    assert _projection(manifest_path)["state"] == "committed"


def test_terminal_manifest_write_failure_retries_close_in_the_same_process(
    monkeypatch,
):
    original_atomic_json = serial_monitor_store._atomic_json
    failed_once = False

    def fail_first_terminal_manifest(path, payload):
        nonlocal failed_once
        if (
            not failed_once
            and path.name == "manifest.json"
            and payload.get("state") == "STOPPED"
            and payload.get("worker_alive") is False
        ):
            failed_once = True
            raise OSError("forced terminal manifest write failure")
        return original_atomic_json(path, payload)

    monkeypatch.setattr(
        serial_monitor_store,
        "_atomic_json",
        fail_first_terminal_manifest,
    )
    started = serial_tools.esp_serial_monitor_start(
        "COM_MONITOR_CLOSE_RETRY",
        session_name="close-retry",
    )
    run_id = started["run_id"]
    payload = b"close-retry-payload"
    MonitorSerial.queue.put(payload)
    _wait_for_monitor(run_id, lambda status: status["bytes_received"] >= len(payload))

    stopped = serial_tools.esp_serial_monitor_stop(run_id)

    assert failed_once is True
    assert stopped["ok"] is True
    assert stopped["monitor"]["worker_alive"] is False
    assert stopped["monitor"]["log_store_closed"] is True
    assert stopped["monitor"]["logging_persistence_state"] == "committed"
    scope = _scope()
    manifest_path = scope.log_root / "serial" / run_id / "manifest.json"
    assert len(_manifest(manifest_path)["chunks"]) == 1
    assert len(
        log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    ) == 1


def test_persisted_stop_reconciles_a_pending_terminal_run():
    scope = _scope()
    run_id = "monitor_20260728_010000_1234abcd"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)

    stopped = serial_tools.esp_serial_monitor_stop(run_id)

    assert stopped["ok"] is True, stopped
    assert stopped["already_terminal"] is True
    assert stopped["monitor"]["logging_persistence_state"] == "committed"
    assert stopped["monitor"]["logging_persisted"] is True
    assert _artifact_marker(manifest_path)["projection"]["state"] == "committed"
    assert len(
        log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    ) == 1
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )["status"] == "cancelled"


def test_persisted_stop_recovers_a_stale_running_manifest():
    scope = _scope()
    run_id = "monitor_20260728_020000_abcdef12"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    manifest = _manifest(manifest_path)
    manifest["state"] = "RUNNING"
    manifest["stopped_at"] = None
    manifest["last_error"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    stopped = serial_tools.esp_serial_monitor_stop(run_id)

    assert stopped["ok"] is True, stopped
    assert stopped["already_terminal"] is False
    assert stopped["recovered_stale_run"] is True
    assert stopped["monitor"]["state"] == "FAILED"
    assert stopped["monitor"]["logging_persistence_state"] == "committed"
    assert stopped["reconciliation"]["ok"] is True
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    assert run["status"] == "failed"
    assert len(
        [
            event
            for event in log_repository.get_run_events(
                scope.database_file,
                project_id=scope.project_id,
                run_id=run_id,
                tail=20,
            )
            if event["phase"] == "complete"
        ]
    ) == 1


@pytest.mark.parametrize(
    (
        "case",
        "expected_state",
        "expected_persisted",
        "expected_version",
        "expected_error",
    ),
    [
        ("pending", "pending", None, 0, None),
        ("sqlite_failed", "failed", False, 0, "sqlite projection failed"),
        ("mirror_failed", "failed", False, ARTIFACT_VERSION, "mirror failed"),
    ],
)
def test_persisted_status_maps_pending_and_failure_states(
    case,
    expected_state,
    expected_persisted,
    expected_version,
    expected_error,
):
    scope = _scope()
    run_id = {
        "pending": "monitor_20260728_020001_00000001",
        "sqlite_failed": "monitor_20260728_020002_00000002",
        "mirror_failed": "monitor_20260728_020003_00000003",
    }[case]
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    if case == "mirror_failed":
        reports = _reconcile(scope, recover_serial_runs(scope.log_root))
        assert reports[0]["ok"] is True
        document = _artifact_marker(manifest_path)
        document["audit_mirror"] = {
            "state": "failed",
            "event_uuid": document["projection"]["event_uuid"],
            "completed_at": None,
            "error": "mirror failed",
        }
        manifest_path.with_name("sqlite-artifacts-v1.json").write_text(
            json.dumps(document),
            encoding="utf-8",
        )
    elif case == "sqlite_failed":
        event_uuid = str(
            uuid5(
                NAMESPACE_URL,
                f"persisted-status:{scope.project_id}:{run_id}",
            )
        )
        document = {
            "format": "esp-mcp-toolchain.serial-sqlite-artifacts",
            "version": ARTIFACT_VERSION,
            "project_id": scope.project_id,
            "run_id": run_id,
            "terminal_marker": None,
            "first_runtime_error": None,
            "projection": {
                "state": "failed",
                "event_uuid": event_uuid,
                "completed_at": None,
                "error": "sqlite projection failed",
            },
            "audit_mirror": {
                "state": "pending",
                "event_uuid": event_uuid,
                "completed_at": None,
                "error": None,
            },
        }
        manifest_path.with_name("sqlite-artifacts-v1.json").write_text(
            json.dumps(document),
            encoding="utf-8",
        )

    result = serial_tools.esp_serial_monitor_status(run_id)

    assert result["ok"] is True
    assert len(result["monitors"]) == 1
    monitor = result["monitors"][0]
    assert monitor["logging_persistence_state"] == expected_state
    assert monitor["logging_persisted"] is expected_persisted
    assert (
        monitor["sqlite_artifacts_reconciliation_version"]
        == expected_version
    )
    assert monitor["sqlite_artifacts_reconciliation_error"] == expected_error


def test_terminal_reconciliation_updates_session_and_latest_mirrors():
    scope = _scope()
    run_id = "monitor_terminal_mirror_probe"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    rows = [
        json.loads(line)
        for line in session_path(run_id, scope.log_root)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    latest = json.loads(latest_path(scope.log_root).read_text(encoding="utf-8"))
    artifact_marker = _artifact_marker(manifest_path)
    assert reports[0]["ok"] is True
    assert [row["phase"] for row in rows] == ["prepare", "complete"]
    assert len({row["event_uuid"] for row in rows}) == 2
    assert latest["run_id"] == run_id
    assert latest["status"] == "cancelled"
    assert artifact_marker["projection"]["state"] == "committed"
    assert artifact_marker["audit_mirror"]["state"] == "committed"


def test_conflicting_session_mirror_is_not_marked_committed():
    scope = _scope()
    run_id = "monitor_conflicting_session_mirror"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    first = _reconcile(scope, recover_serial_runs(scope.log_root))
    assert first[0]["ok"] is True
    assert SerialMonitorManager().persisted_status(
        scope.log_root,
        run_id,
    )["logging_persistence_state"] == "committed"
    mirror_path = session_path(run_id, scope.log_root)
    rows = [
        json.loads(line)
        for line in mirror_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    completion = [row for row in rows if row["phase"] == "complete"]
    assert len(completion) == 1
    completion[0]["message"] = "FORGED MIRROR CONTENT"
    mirror_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    forged_status = SerialMonitorManager().persisted_status(
        scope.log_root,
        run_id,
    )
    assert forged_status["logging_persistence_state"] == "failed"
    assert forged_status["sqlite_artifacts_reconciliation_version"] == 1
    assert "conflict" in (
        forged_status["sqlite_artifacts_reconciliation_error"].lower()
    )

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert len(reports) == 1
    assert reports[0]["ok"] is True
    assert reports[0]["database_persisted"] is True
    assert reports[0]["audit_mirror_persisted"] is False
    assert "conflicts" in reports[0]["logging_warning"]
    marker = _artifact_marker(manifest_path)
    assert marker["projection"]["state"] == "committed"
    assert marker["audit_mirror"]["state"] == "failed"


def test_mirror_failure_keeps_sqlite_committed_and_remains_retryable(
    monkeypatch,
):
    scope = _scope()
    run_id = "monitor_mirror_retry_probe"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    original_mirror = (
        serial_monitor_backend.mirror_committed_event_and_refresh_latest
    )
    monkeypatch.setattr(
        serial_monitor_backend,
        "mirror_committed_event_and_refresh_latest",
        lambda *_args, **_kwargs: {
            "ok": False,
            "session_persisted": False,
            "latest_persisted": False,
            "warnings": ["session mirror: forced failure"],
        },
    )

    first_reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    first_marker = _artifact_marker(manifest_path)
    assert first_reports[0]["ok"] is True
    assert first_reports[0]["database_persisted"] is True
    assert first_reports[0]["audit_mirror_persisted"] is False
    assert first_marker["projection"]["state"] == "committed"
    assert first_marker["audit_mirror"]["state"] == "failed"
    assert len(
        log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    ) == 1
    first_events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )

    monkeypatch.setattr(
        serial_monitor_backend,
        "mirror_committed_event_and_refresh_latest",
        original_mirror,
    )
    second_reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert second_reports[0]["ok"] is True
    assert _artifact_marker(manifest_path)["audit_mirror"]["state"] == "committed"
    assert log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    ) == first_events
    rows = [
        json.loads(line)
        for line in session_path(run_id, scope.log_root)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert [row["phase"] for row in rows] == ["prepare", "complete"]


def test_committed_marker_does_not_short_circuit_a_missing_sqlite_bundle():
    scope = _scope()
    run_id = "monitor_20260728_030000_deadbeef"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    event_uuid = str(
        uuid5(
            NAMESPACE_URL,
            (
                "esp-mcp-toolchain:serial-monitor-terminal:"
                f"v1:{scope.project_id}:{run_id}"
            ),
        )
    )
    marker_path = manifest_path.with_name("sqlite-artifacts-v1.json")
    marker_path.write_text(
        json.dumps(
            {
                "format": "esp-mcp-toolchain.serial-sqlite-artifacts",
                "version": ARTIFACT_VERSION,
                "project_id": scope.project_id,
                "run_id": run_id,
                "terminal_marker": {
                    "version": 1,
                    "marker_id": event_uuid,
                    "event_uuid": event_uuid,
                    "project_id": scope.project_id,
                    "run_id": run_id,
                    "state": "STOPPED",
                    "run_status": "cancelled",
                    "terminal_at": "2026-07-28T01:00:00+00:00",
                    "level": "info",
                    "tool": "esp_serial_monitor",
                    "source": "serial_monitor_terminal",
                    "message": "Serial monitor stopped.",
                    "last_error": None,
                    "detected_error": None,
                    "detected_error_at": None,
                    "stale_recovery": False,
                },
                "first_runtime_error": None,
                "projection": {
                    "state": "committed",
                    "event_uuid": event_uuid,
                    "completed_at": "2026-07-28T01:00:01+00:00",
                    "error": None,
                },
                "audit_mirror": {
                    "state": "committed",
                    "event_uuid": event_uuid,
                    "completed_at": "2026-07-28T01:00:01+00:00",
                    "error": None,
                },
            }
        ),
        encoding="utf-8",
    )
    forged_status = SerialMonitorManager().persisted_status(
        scope.log_root,
        run_id,
    )
    assert forged_status["logging_persistence_state"] == "failed"
    assert forged_status["logging_persisted"] is False
    assert forged_status["sqlite_artifacts_reconciliation_version"] == 0
    assert "missing or conflicts" in (
        forged_status["sqlite_artifacts_reconciliation_error"]
    )

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert reports[0]["ok"] is True
    assert len(
        log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    ) == 1
    assert [
        event["phase"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=20,
        )
    ] == ["prepare", "complete"]
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )["status"] == "cancelled"


def test_stale_finalized_chunk_missing_from_manifest_is_recovered():
    scope = _scope()
    run_id = "monitor_orphan_finalized_chunk"
    payload = b"renamed-before-manifest-update"
    manifest_path, chunk_path = _terminal_manifest(
        scope,
        run_id=run_id,
        state="RUNNING",
        payload=payload,
    )
    stored_before = _manifest(manifest_path)
    stored_before["chunks"] = []
    manifest_path.write_text(json.dumps(stored_before), encoding="utf-8")

    recovered = recover_serial_runs(
        scope.log_root,
        project_id=scope.project_id,
    )

    assert chunk_path.is_file()
    assert len(recovered) == 1
    assert len(recovered[0]["chunks"]) == 1
    assert recovered[0]["chunks"][0]["chunk_id"] == 1
    assert recovered[0]["chunks"][0]["sha256"] == hashlib.sha256(payload).hexdigest()

    _reconcile(scope, recovered)

    raw_logs = log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    stored_after = _manifest(manifest_path)
    assert len(raw_logs) == 1
    assert raw_logs[0]["path"] == f"serial/{run_id}/chunk-000001.bin"
    assert raw_logs[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert "sqlite_artifact_projection" not in stored_after
    assert _artifact_marker(manifest_path)["projection"]["state"] == "committed"


def test_atomic_artifact_failure_leaves_monitor_retryable(monkeypatch):
    scope = _scope()
    manifest_path, _chunk = _terminal_manifest(
        scope,
        run_id="monitor_artifact_retry",
    )
    recovered = recover_serial_runs(scope.log_root)
    original = log_repository.finalize_existing_run_with_artifacts

    def fail_artifacts(*args, artifacts=log_repository.EventArtifacts(), **kwargs):
        if artifacts.raw_logs or artifacts.errors:
            raise log_repository.ArtifactProjectionError("forced monitor artifact failure")
        return original(*args, artifacts=artifacts, **kwargs)

    monkeypatch.setattr(
        log_repository,
        "finalize_existing_run_with_artifacts",
        fail_artifacts,
    )
    _reconcile(scope, recovered)

    assert _manifest(manifest_path)["sqlite_artifacts_reconciliation_version"] == 0
    assert _projection(manifest_path)["state"] == "failed"
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id="monitor_artifact_retry",
    ) == []
    assert [
        event["phase"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id="monitor_artifact_retry",
            tail=20,
        )
    ] == ["prepare"]
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id="monitor_artifact_retry",
    )["status"] == "running"

    monkeypatch.setattr(
        log_repository,
        "finalize_existing_run_with_artifacts",
        original,
    )
    _reconcile(scope, recover_serial_runs(scope.log_root))
    assert "sqlite_artifacts_reconciliation_version" not in _manifest(
        manifest_path
    )
    assert _projection(manifest_path)["state"] == "committed"
    assert len(
        log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id="monitor_artifact_retry",
        )
    ) == 1
    assert [
        event["phase"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id="monitor_artifact_retry",
            tail=20,
        )
    ] == ["prepare", "complete"]
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id="monitor_artifact_retry",
    )["status"] == "cancelled"


def test_second_raw_insert_failure_rolls_back_terminal_bundle(monkeypatch):
    scope = _scope()
    run_id = "monitor_second_raw_failure"
    manifest_path, first_chunk = _terminal_manifest(scope, run_id=run_id)
    second_payload = b"second-chunk"
    second_chunk = first_chunk.parent / "chunk-000002.bin"
    second_chunk.write_bytes(second_payload)
    manifest = _manifest(manifest_path)
    manifest["chunks"].append(
        {
            "chunk_id": 2,
            "path": str(second_chunk),
            "byte_length": len(second_payload),
            "sha256": hashlib.sha256(second_payload).hexdigest(),
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    recovered = recover_serial_runs(scope.log_root)
    original_insert = log_repository.insert_raw_log
    calls = 0

    def fail_second_insert(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RawLogRepositoryError("forced second raw insert failure")
        return original_insert(*args, **kwargs)

    monkeypatch.setattr(log_repository, "insert_raw_log", fail_second_insert)
    _reconcile(scope, recovered)

    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == []
    assert [
        event["phase"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=20,
        )
    ] == ["prepare"]
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    assert run["status"] == "running"
    assert run["next_sequence_no"] == 2
    assert _projection(manifest_path)["state"] == "failed"

    monkeypatch.setattr(log_repository, "insert_raw_log", original_insert)
    _reconcile(scope, recover_serial_runs(scope.log_root))
    assert len(
        log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    ) == 2
    assert _projection(manifest_path)["state"] == "committed"


@pytest.mark.parametrize("corruption", ["path", "length", "sha256"])
def test_monitor_reconciliation_rejects_corrupt_chunk_metadata(corruption, tmp_path):
    scope = _scope()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    kwargs = {
        "path": {"path_value": str(outside)},
        "length": {"byte_length": len(b"monitor-chunk") + 1},
        "sha256": {"sha256": "0" * 64},
    }[corruption]
    manifest_path, _chunk = _terminal_manifest(
        scope,
        run_id=f"monitor_corrupt_{corruption}",
        last_error={
            "error_kind": "serial_disconnected",
            "message": "device disconnected",
        },
        **kwargs,
    )
    sequence_before = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=f"monitor_corrupt_{corruption}",
    )["next_sequence_no"]

    _reconcile(scope, recover_serial_runs(scope.log_root))

    stored = _manifest(manifest_path)
    assert stored["sqlite_artifacts_reconciliation_version"] == 0
    artifact_marker = _artifact_marker(manifest_path)
    assert artifact_marker["projection"]["error"]
    assert artifact_marker["projection"]["state"] == "failed"
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=f"monitor_corrupt_{corruption}",
    ) == []
    assert log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=f"monitor_corrupt_{corruption}",
    ) == []
    assert [
        event["phase"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=f"monitor_corrupt_{corruption}",
            tail=20,
        )
    ] == ["prepare"]
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=f"monitor_corrupt_{corruption}",
    )
    assert run["status"] == "running"
    assert run["next_sequence_no"] == sequence_before


def test_marker_failure_retries_after_run_is_already_terminal(monkeypatch):
    scope = _scope()
    run_id = "monitor_marker_retry"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    recovered = recover_serial_runs(scope.log_root)
    original_marker = serial_monitor_backend.mark_serial_run_artifacts_reconciled

    monkeypatch.setattr(
        serial_monitor_backend,
        "mark_serial_run_artifacts_reconciled",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("forced artifact marker failure")
        ),
    )
    _reconcile(scope, recovered)

    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )["status"] == "cancelled"
    assert _manifest(manifest_path)["sqlite_artifacts_reconciliation_version"] == 0
    first_events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )
    first_raw = log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )

    monkeypatch.setattr(
        serial_monitor_backend,
        "mark_serial_run_artifacts_reconciled",
        original_marker,
    )
    _reconcile(scope, recover_serial_runs(scope.log_root))

    assert "sqlite_artifacts_reconciliation_version" not in _manifest(
        manifest_path
    )
    assert _projection(manifest_path)["state"] == "committed"
    assert log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    ) == first_events
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == first_raw


def test_terminal_last_error_and_detected_error_have_distinct_stable_occurrences():
    scope = _scope()
    run_id = "monitor_distinct_errors"
    manifest_path, _chunk = _terminal_manifest(
        scope,
        run_id=run_id,
        state="FAILED",
        last_error={
            "error_kind": "serial_disconnected",
            "exception_type": "OSError",
            "message": "device disconnected",
        },
        detected_error={
            "has_error": True,
            "error_kind": "micropython_traceback",
            "file": "main.py",
            "line": 12,
            "exception_type": "RuntimeError",
            "message": "application failed",
            "recoverable": True,
        },
    )

    _reconcile(scope, recover_serial_runs(scope.log_root))

    stored = _manifest(manifest_path)
    artifact_marker = _artifact_marker(manifest_path)
    event_uuid = artifact_marker["terminal_marker"]["event_uuid"]
    errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    assert {error["error_kind"] for error in errors} == {
        "serial_disconnected",
        "micropython_traceback",
    }
    assert {error["error_id"] for error in errors} == {
        stable_error_id(
            project_id=scope.project_id,
            run_id=run_id,
            occurrence_key=f"event:{event_uuid}:last_error",
            error_kind="serial_disconnected",
            file=None,
            line=None,
            column=None,
            exception_type="OSError",
            message="device disconnected",
            raw_text=None,
        ),
        stable_error_id(
            project_id=scope.project_id,
            run_id=run_id,
            occurrence_key=f"event:{event_uuid}:detected_error",
            error_kind="micropython_traceback",
            file="main.py",
            line=12,
            column=None,
            exception_type="RuntimeError",
            message="application failed",
            raw_text=None,
        ),
    }
    raw_logs = log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    assert [
        {
            "kind": record["kind"],
            "path": record["path"],
            "sha256": record["sha256"],
        }
        for record in raw_logs
    ] == [
        {
            "kind": "serial_monitor_chunk",
            "path": f"serial/{run_id}/chunk-000001.bin",
            "sha256": hashlib.sha256(b"monitor-chunk").hexdigest(),
        }
    ]
    assert {
        (
            error["error_kind"],
            error["file"],
            error["line"],
            error["column"],
            error["exception_type"],
            error["message"],
            error["raw_text"],
            error["recoverable"],
        )
        for error in errors
    } == {
        (
            "serial_disconnected",
            None,
            None,
            None,
            "OSError",
            "device disconnected",
            None,
            None,
        ),
        (
            "micropython_traceback",
            "main.py",
            12,
            None,
            "RuntimeError",
            "application failed",
            None,
            True,
        ),
    }
    expected_bundle = {
        "raw_logs": [
            {
                "kind": "serial_monitor_chunk",
                "path": f"serial/{run_id}/chunk-000001.bin",
                "sha256": hashlib.sha256(b"monitor-chunk").hexdigest(),
            }
        ],
        "errors": [
            {
                "occurrence_key": f"event:{event_uuid}:last_error",
                "error_kind": "serial_disconnected",
                "file": None,
                "line": None,
                "column": None,
                "exception_type": "OSError",
                "message": "device disconnected",
                "raw_text": None,
                "recoverable": None,
                "created_at": None,
            },
            {
                "occurrence_key": f"event:{event_uuid}:detected_error",
                "error_kind": "micropython_traceback",
                "file": "main.py",
                "line": 12,
                "column": None,
                "exception_type": "RuntimeError",
                "message": "application failed",
                "raw_text": None,
                "recoverable": True,
                "created_at": None,
            },
        ],
    }
    expected_digest = hashlib.sha256(
        json.dumps(
            expected_bundle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    completion = [
        event
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=20,
        )
        if event["phase"] == "complete"
    ]
    assert len(completion) == 1
    assert (
        completion[0]["payload_json"]["artifact_bundle_sha256"]
        == expected_digest
    )
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    assert (
        run["payload_json"]["monitor_artifact_bundle_sha256"]
        == expected_digest
    )
    first_raw_logs = list(raw_logs)
    first_errors = list(errors)
    first_completion = list(completion)

    retry_reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert retry_reports[0]["ok"] is True
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == first_raw_logs
    assert log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == first_errors
    assert [
        event
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=20,
        )
        if event["phase"] == "complete"
    ] == first_completion
    assert "sqlite_artifact_projection" not in stored
    assert artifact_marker["projection"]["state"] == "committed"


def test_reconciliation_does_not_create_an_orphan_sqlite_run():
    scope = _scope()
    start_run("test_sentinel", run_id="sentinel", scope=scope)
    run_id = "monitor_orphan_manifest"
    run_dir = scope.log_root / "serial" / run_id
    run_dir.mkdir(parents=True)
    payload = b"orphan"
    chunk = run_dir / "chunk-000001.bin"
    chunk.write_bytes(payload)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "run_id": run_id,
                "project_id": scope.project_id,
                "state": "STOPPED",
                "stopped_at": "2026-07-28T01:00:00+00:00",
                "chunks": [
                    {
                        "chunk_id": 1,
                        "path": str(chunk),
                        "byte_length": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
                "sqlite_artifacts_reconciliation_version": 0,
            }
        ),
        encoding="utf-8",
    )

    _reconcile(scope, recover_serial_runs(scope.log_root))

    stored = _manifest(manifest_path)
    assert log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) is None
    assert stored["sqlite_artifacts_reconciliation_version"] == 0
    artifact_marker = _artifact_marker(manifest_path)
    assert artifact_marker["projection"]["state"] == "failed"
    assert "run" in artifact_marker["projection"]["error"].lower()


def test_reconciliation_refuses_wrong_sqlite_task_type():
    scope = _scope()
    run_id = "monitor_wrong_task_type"
    manifest_path, _chunk = _terminal_manifest(
        scope,
        run_id=run_id,
        task_type="program_execution",
    )

    _reconcile(scope, recover_serial_runs(scope.log_root))

    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    assert run["task_type"] == "program_execution"
    assert run["status"] == "running"
    assert [
        event["phase"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=20,
        )
    ] == ["prepare"]
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == []
    assert _projection(manifest_path)["state"] == "failed"
    assert "task_type" in _projection(manifest_path)["error"]


def test_monitor_start_surfaces_a_bounded_prior_recovery_failure():
    scope = _scope()
    failed_run_id = "monitor_prior_recovery_failure"
    failed_run_dir = scope.log_root / "serial" / failed_run_id
    failed_run_dir.mkdir(parents=True)
    (failed_run_dir / "manifest.json").write_text(
        "{not-valid-json",
        encoding="utf-8",
    )

    started = serial_tools.esp_serial_monitor_start(
        "COM_RECOVERY_REPORT",
        session_name="recovery-report",
    )
    try:
        assert started["ok"] is True, started
        assert started["recovery_report_count"] == 1
        assert started["recovery_failure_count"] == 1
        assert started["recovery_reports"] == [
            {
                "ok": False,
                "run_id": failed_run_id,
                "error_kind": "monitor_artifact_recovery_failed",
                "message": started["recovery_reports"][0]["message"],
                "database_persisted": None,
                "audit_mirror_persisted": None,
                "recoverable": None,
            }
        ]
        assert "invalid" in started["recovery_reports"][0]["message"].lower()
        assert "prior monitor run" in started["recovery_warning"]
    finally:
        if started.get("ok") and started.get("run_id"):
            serial_tools.esp_serial_monitor_stop(started["run_id"])


@pytest.mark.parametrize("extra_kind", ["raw", "error"])
def test_reconciliation_rejects_artifacts_outside_the_canonical_bundle(
    extra_kind,
):
    scope = _scope()
    run_id = f"monitor_extra_{extra_kind}_artifact"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    extra_id = str(
        uuid5(
            NAMESPACE_URL,
            f"extra-monitor-artifact:{extra_kind}:{scope.project_id}:{run_id}",
        )
    )
    if extra_kind == "raw":
        log_repository.register_raw_log(
            scope.database_file,
            project_id=scope.project_id,
            raw_log_id=extra_id,
            run_id=run_id,
            kind="unexpected_raw",
            path=f"serial/{run_id}/unexpected.bin",
            created_at="2026-07-28T00:59:00+00:00",
            sha256=hashlib.sha256(b"unexpected").hexdigest(),
        )
    else:
        log_repository.register_error(
            scope.database_file,
            project_id=scope.project_id,
            error_id=extra_id,
            run_id=run_id,
            error_kind="unexpected_error",
            file=None,
            line=None,
            column=None,
            exception_type=None,
            message="unexpected",
            raw_text=None,
            recoverable=None,
            created_at="2026-07-28T00:59:00+00:00",
        )

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert len(reports) == 1
    assert reports[0]["ok"] is False
    assert "canonical bundle" in reports[0]["message"].lower()
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    assert run["status"] == "running"
    assert run["next_sequence_no"] == 2
    assert [
        event["phase"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=20,
        )
    ] == ["prepare"]
    raw_logs = log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    errors = log_repository.get_run_errors(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    assert [record["raw_log_id"] for record in raw_logs] == (
        [extra_id] if extra_kind == "raw" else []
    )
    assert [record["error_id"] for record in errors] == (
        [extra_id] if extra_kind == "error" else []
    )
    artifact_marker = _artifact_marker(manifest_path)
    assert artifact_marker["projection"]["state"] == "failed"
    assert artifact_marker["audit_mirror"]["state"] != "committed"


def test_concurrent_reconciliation_commits_one_deterministic_bundle():
    scope = _scope()
    run_id = "monitor_concurrent_reconciliation"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    recovered = recover_serial_runs(scope.log_root)
    start_gate = threading.Barrier(3)
    outcomes: Queue = Queue()

    def reconcile_once() -> None:
        try:
            start_gate.wait()
            outcomes.put(("reports", _reconcile(scope, recovered)))
        except BaseException as exc:
            outcomes.put(("exception", exc))

    workers = [
        threading.Thread(target=reconcile_once, daemon=True)
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    start_gate.wait()
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive()
    recorded = [outcomes.get_nowait() for _ in range(2)]
    exceptions = [
        value for kind, value in recorded if kind == "exception"
    ]
    reports = [
        report
        for kind, values in recorded
        if kind == "reports"
        for report in values
    ]
    assert exceptions == []
    assert len(reports) == 2
    assert all(type(report.get("ok")) is bool for report in reports)
    successful = [report for report in reports if report["ok"] is True]
    failed = [report for report in reports if report["ok"] is False]
    assert len(successful) + len(failed) == 2
    assert (len(successful), len(failed)) in {(2, 0), (1, 1)}
    assert successful
    assert all(report["database_persisted"] is True for report in successful)
    assert all(
        report["audit_mirror_persisted"] is True for report in successful
    )
    assert len(
        {report["event"]["event_uuid"] for report in successful}
    ) == 1
    assert all(
        report["error_kind"] == "monitor_artifact_reconciliation_busy"
        and report["recoverable"] is True
        for report in failed
    ), failed

    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    assert len([event for event in events if event["phase"] == "complete"]) == 1
    assert len(
        log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    ) == 1
    assert run["status"] == "cancelled"
    assert run["next_sequence_no"] == 3
    assert _projection(manifest_path)["state"] == "committed"

    events_before_retry = list(events)
    raw_before_retry = log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    retry_reports = _reconcile(scope, recover_serial_runs(scope.log_root))
    assert len(retry_reports) == 1
    assert retry_reports[0]["ok"] is True
    assert log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    ) == events_before_retry
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == raw_before_retry


def test_recovery_can_hold_the_run_lease_through_terminal_reconciliation():
    scope = _scope()
    run_id = "monitor_full_lifecycle_lease"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    run_dir = manifest_path.parent

    def reconcile_while_borrowed(
        manifest: dict,
        borrowed_lease: SerialRunReconciliationLease,
    ) -> dict:
        assert isinstance(borrowed_lease, SerialRunReconciliationLease)
        with pytest.raises(SerialLogReconciliationBusy):
            SerialRunReconciliationLease.acquire(run_dir)
        report = SerialMonitorManager._reconcile_recovered_manifest(
            _binding(scope),
            manifest,
            borrowed_lease=borrowed_lease,
        )
        assert report is not None
        return report

    reports = recover_serial_runs(
        scope.log_root,
        project_id=scope.project_id,
        reconciliation_consumer=reconcile_while_borrowed,
    )

    assert len(reports) == 1
    assert reports[0]["ok"] is True, reports
    replacement = SerialRunReconciliationLease.acquire(run_dir)
    replacement.release()
    assert _projection(manifest_path)["state"] == "committed"


@pytest.mark.skipif(
    os.name != "nt",
    reason="Windows byte-range locks make the zero-length race observable.",
)
def test_windows_zero_length_locked_lease_reports_a_busy_contender(tmp_path):
    run_dir = tmp_path / "monitor-zero-length-lock-race"
    run_dir.mkdir()
    owner = SerialRunReconciliationLease.acquire(run_dir)
    try:
        assert owner.held is True
        owner._handle.seek(0)
        owner._handle.truncate(0)
        owner._handle.flush()
        os.fsync(owner._handle.fileno())
        assert owner.path.stat().st_size == 0

        with pytest.raises(SerialLogReconciliationBusy):
            SerialRunReconciliationLease.acquire(run_dir)
    finally:
        owner.release()
    replacement = SerialRunReconciliationLease.acquire(run_dir)
    replacement.release()


def test_reconciliation_lease_uses_an_os_lock_without_unlinking_the_owner(
    monkeypatch,
):
    scope = _scope()
    run_id = "monitor_os_lock_lease"
    manifest_path, _chunk = _terminal_manifest(scope, run_id=run_id)
    run_dir = manifest_path.parent
    lease = SerialRunReconciliationLease.acquire(run_dir)
    lock_path = lease.path
    first_status = lock_path.stat()
    first_identity = (first_status.st_size, first_status.st_mtime_ns)
    original_unlink = Path.unlink
    if os.name == "nt":
        with pytest.raises(OSError):
            original_unlink(lock_path)
        assert lock_path.exists()

    def refuse_lock_unlink(path: Path, *args, **kwargs):
        if path == lock_path:
            raise AssertionError("An active reconciliation lock must not be unlinked.")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_lock_unlink)
    with pytest.raises(SerialLogReconciliationBusy):
        SerialRunReconciliationLease.acquire(run_dir)
    current_status = lock_path.stat()
    assert (current_status.st_size, current_status.st_mtime_ns) == first_identity

    lease.release()

    assert lock_path.exists()
    first_owner = json.loads(lock_path.read_text(encoding="utf-8"))
    assert first_owner["lock_id"] == lease.lock_id
    replacement = SerialRunReconciliationLease.acquire(run_dir)
    assert replacement.lock_id != lease.lock_id
    replacement.release()
    assert lock_path.exists()


def test_legacy_stale_complete_event_is_reused_without_a_second_completion():
    scope = _scope()
    run_id = "monitor_legacy_stale_event"
    last_error = {
        "error_kind": "stale_monitor_recovered",
        "message": "A previous monitor process ended without completing cleanup.",
    }
    manifest_path, _chunk = _terminal_manifest(
        scope,
        run_id=run_id,
        state="FAILED",
        last_error=last_error,
    )
    legacy_uuid = str(
        uuid5(
            NAMESPACE_URL,
            f"esp-mcp-toolchain:stale-monitor:{scope.project_id}:{run_id}",
        )
    )
    current_uuid = str(
        uuid5(
            NAMESPACE_URL,
            (
                "esp-mcp-toolchain:serial-monitor-terminal:"
                f"v1:{scope.project_id}:{run_id}"
            ),
        )
    )
    legacy_payload = {"state": "FAILED", "last_error": last_error}
    legacy_event = write_event(
        "esp_serial_monitor",
        "error",
        last_error["message"],
        legacy_payload,
        run_id=run_id,
        ts="2026-07-28T01:00:00+00:00",
        phase="complete",
        event_uuid=legacy_uuid,
        source="monitor_recovery",
        scope=scope,
    )
    historical_ended_at = "2026-07-28T01:05:00+00:00"
    historical_run = log_repository.finish_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        status="failed",
        ended_at=historical_ended_at,
        summary=last_error["message"],
        payload=legacy_payload,
    )
    events_before = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert len(reports) == 1
    assert reports[0]["ok"] is True, reports
    events_after = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )
    run_after = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    complete_events = [
        event for event in events_after if event["phase"] == "complete"
    ]
    assert legacy_event["event_uuid"] == legacy_uuid
    assert [event["event_uuid"] for event in complete_events] == [legacy_uuid]
    assert all(event["event_uuid"] != current_uuid for event in events_after)
    assert len(events_after) == len(events_before)
    assert run_after["ended_at"] == historical_ended_at
    assert run_after["summary"] == historical_run["summary"]
    assert run_after["payload_json"] == historical_run["payload_json"]
    assert run_after["next_sequence_no"] == historical_run["next_sequence_no"]
    assert len(
        log_repository.get_run_raw_logs(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
        )
    ) == 1
    marker = _artifact_marker(manifest_path)
    assert marker["terminal_marker"]["event_uuid"] == legacy_uuid
    assert marker["projection"]["event_uuid"] == legacy_uuid
    assert marker["projection"]["state"] == "committed"
    assert _manifest(manifest_path)["sqlite_reconciled"] is True


def test_legacy_stale_event_is_refused_when_a_later_event_exists():
    scope = _scope()
    run_id = "monitor_legacy_stale_not_last"
    last_error = {
        "error_kind": "stale_monitor_recovered",
        "message": "A previous monitor process ended without completing cleanup.",
    }
    _terminal_manifest(
        scope,
        run_id=run_id,
        state="FAILED",
        last_error=last_error,
    )
    legacy_uuid = str(
        uuid5(
            NAMESPACE_URL,
            f"esp-mcp-toolchain:stale-monitor:{scope.project_id}:{run_id}",
        )
    )
    write_event(
        "esp_serial_monitor",
        "error",
        last_error["message"],
        {"state": "FAILED", "last_error": last_error},
        run_id=run_id,
        ts="2026-07-28T01:00:00+00:00",
        phase="complete",
        event_uuid=legacy_uuid,
        source="monitor_recovery",
        scope=scope,
    )
    write_event(
        "post_terminal_probe",
        "warning",
        "later event",
        {},
        run_id=run_id,
        ts="2026-07-28T01:01:00+00:00",
        phase="execute",
        source="test",
        scope=scope,
    )
    log_repository.finish_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        status="failed",
        ended_at="2026-07-28T01:05:00+00:00",
        summary=last_error["message"],
        payload={"state": "FAILED", "last_error": last_error},
    )

    reports = _reconcile(scope, recover_serial_runs(scope.log_root))

    assert len(reports) == 1
    assert reports[0]["ok"] is False
    assert "last event" in reports[0]["message"]
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == []
    assert [
        event["event_uuid"]
        for event in log_repository.get_run_events(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            tail=20,
        )
        if event["phase"] == "complete"
    ] == [legacy_uuid]


def test_recovery_refuses_a_reparse_run_directory_without_external_mutation(
    tmp_path,
):
    scope = _scope()
    serial_root = scope.log_root / "serial"
    serial_root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside-run"
    outside.mkdir()
    part = outside / "chunk-000001.bin.part"
    part.write_bytes(b"outside")
    (outside / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "run_id": "monitor_linked_run",
                "project_id": scope.project_id,
                "state": "RUNNING",
                "chunks": [],
            }
        ),
        encoding="utf-8",
    )
    linked_run = serial_root / "monitor_linked_run"
    original_manifest = (outside / "manifest.json").read_bytes()
    _directory_reparse(linked_run, outside)

    reports = recover_serial_runs(scope.log_root)
    assert len(reports) == 1
    assert "reparse point" in reports[0][
        "_sqlite_artifact_recovery_error"
    ].lower()
    assert part.read_bytes() == b"outside"
    assert not (outside / "chunk-000001.bin").exists()
    assert (outside / "manifest.json").read_bytes() == original_manifest


def test_reconciliation_refuses_a_reparse_chunk(tmp_path):
    scope = _scope()
    run_id = "monitor_linked_chunk"
    start_run(
        "serial_monitor",
        run_id=run_id,
        selected_port="COM_RECONCILE",
        scope=scope,
    )
    run_dir = scope.log_root / "serial" / run_id
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside-chunk")
    linked_chunk = run_dir / "chunk-000001.bin"
    _symlink_or_skip(linked_chunk, outside)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "run_id": run_id,
                "project_id": scope.project_id,
                "state": "STOPPED",
                "stopped_at": "2026-07-28T01:00:00+00:00",
                "chunks": [
                    {
                        "chunk_id": 1,
                        "path": str(linked_chunk),
                        "byte_length": outside.stat().st_size,
                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    }
                ],
                "sqlite_artifacts_reconciliation_version": 0,
            }
        ),
        encoding="utf-8",
    )

    original_manifest = manifest_path.read_bytes()
    recovered = recover_serial_runs(scope.log_root)
    assert len(recovered) == 1
    assert "reparse" in recovered[0][
        "_sqlite_artifact_recovery_error"
    ].lower()

    reports = _reconcile(scope, recovered)

    stored = _manifest(manifest_path)
    assert stored["sqlite_artifacts_reconciliation_version"] == 0
    assert manifest_path.read_bytes() == original_manifest
    assert len(reports) == 1
    assert reports[0]["ok"] is False
    assert reports[0]["artifact_marker"] is None
    assert "reparse" in reports[0]["message"].lower()
    assert not manifest_path.with_name("sqlite-artifacts-v1.json").exists()
    assert log_repository.get_run_raw_logs(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    ) == []
    assert outside.read_bytes() == b"outside-chunk"


def test_persisted_read_refuses_reparse_records_file(tmp_path):
    run_dir = tmp_path / "monitor-linked-records"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": run_dir.name, "state": "STOPPED"}),
        encoding="utf-8",
    )
    outside = tmp_path / "outside-records.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    _symlink_or_skip(run_dir / "records.jsonl", outside)

    with pytest.raises(SerialLogStoreError, match="reparse"):
        read_persisted_records(
            run_dir,
            after_seq=None,
            max_bytes=1024,
            representation="text",
        )
