from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from esp_mcp_toolchain.backends import serial_monitor_backend, serial_monitor_store
from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.event_repository import normalize_timestamp
from esp_mcp_toolchain.tools.log_tools import LogScope, finish_run, start_run


TERMINAL_AT = "2026-07-28T08:00:00.930Z"
EVENT_AT = "2026-07-28T08:00:00+00:00"
ADAPTER_ID = "historical_monitor_manifest_v1"


def _scope() -> LogScope:
    return LogScope.active()


def _run_dir(scope: LogScope, run_id: str) -> Path:
    return scope.log_root / "serial" / run_id


def _write_history(
    scope: LogScope,
    *,
    run_id: str,
    format_version: object = 1,
    include_format_version: bool = True,
    state: str = "STOPPED",
    payloads: tuple[bytes, ...] = (b"historical-monitor-chunk",),
    last_error: dict | None = None,
    detected_error: dict | None = None,
    legacy_paths: tuple[str, ...] | None = None,
    manifest_updates: dict | None = None,
) -> tuple[Path, list[Path], dict]:
    run_dir = _run_dir(scope, run_id)
    run_dir.mkdir(parents=True)
    chunks: list[dict] = []
    chunk_paths: list[Path] = []
    for chunk_id, payload in enumerate(payloads, start=1):
        name = f"chunk-{chunk_id:06d}.bin"
        chunk_path = run_dir / name
        chunk_path.write_bytes(payload)
        chunk_paths.append(chunk_path)
        chunk = {
            "chunk_id": chunk_id,
            "byte_length": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        if format_version == 2:
            chunk["name"] = name
        else:
            supplied = (
                legacy_paths[chunk_id - 1]
                if legacy_paths is not None
                else (
                    "Z:\\moved-project\\logs\\serial\\"
                    f"{run_id}\\{name}"
                )
            )
            chunk["path"] = supplied
        chunks.append(chunk)
    manifest = {
        "run_id": run_id,
        "project_id": scope.project_id,
        "session_name": "historical-monitor",
        "port": "COM_HISTORY",
        "baudrate": 115200,
        "state": state,
        "process_owner": {
            "pid": os.getpid(),
            "process_token": "historical-owner-token",
            "process_started": "historical-owner-start",
        },
        "records_path": (
            "Z:\\moved-project\\logs\\serial\\"
            f"{run_id}\\records.jsonl"
        ),
        "chunks": chunks,
        "persisted_bytes": sum(len(payload) for payload in payloads),
        "stopped_at": TERMINAL_AT,
        "last_error": last_error,
        "detected_error": detected_error,
        "sqlite_reconciled": False,
    }
    if include_format_version:
        manifest["format_version"] = format_version
    if manifest_updates:
        manifest.update(manifest_updates)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path, chunk_paths, manifest


def _replace_manifest(path: Path, manifest: dict) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _tree_snapshot(root: Path) -> dict[str, tuple]:
    if not root.exists():
        return {}
    snapshot: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            status = path.lstat()
            snapshot[relative] = (
                "dir",
                int(status.st_dev),
                int(status.st_ino),
                int(status.st_mtime_ns),
            )
        else:
            status = path.lstat()
            raw = path.read_bytes()
            snapshot[relative] = (
                "file",
                len(raw),
                hashlib.sha256(raw).hexdigest(),
                int(status.st_mtime_ns),
            )
    return snapshot


def _resolve(
    scope: LogScope,
    *,
    run_id: str,
    event_uuid: str | None,
):
    resolver = getattr(
        serial_monitor_backend,
        "resolve_historical_monitor_artifacts",
    )
    return resolver(
        scope,
        run_id=run_id,
        event_uuid=event_uuid,
    )


def _assert_resolution_error(
    scope: LogScope,
    *,
    run_id: str,
    event_uuid: str | None = None,
    error_kind: str = "historical_monitor_resolution_failed",
) -> None:
    requested_uuid = event_uuid if event_uuid is not None else str(uuid4())
    with pytest.raises(Exception) as captured:
        _resolve(
            scope,
            run_id=run_id,
            event_uuid=requested_uuid,
        )
    assert getattr(captured.value, "error_kind", None) == error_kind


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
    assert getattr(link.lstat(), "st_file_attributes", 0) & 0x400


def _prepare_terminal_event(
    scope: LogScope,
    *,
    run_id: str,
    event_uuid: str,
    status: str,
    state: str,
    last_error: dict | None,
) -> None:
    start_run(
        "serial_monitor",
        run_id=run_id,
        summary="historical monitor",
        payload={"state": state, "last_error": last_error},
        scope=scope,
    )
    event, inserted = log_repository.append_event(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        event_uuid=event_uuid,
        ts=EVENT_AT,
        phase="complete",
        level="info" if state == "STOPPED" else "error",
        tool="esp_serial_monitor",
        source="esp32",
        message=(
            "Serial monitor stopped."
            if state == "STOPPED"
            else "Serial monitor failed."
        ),
        payload={"state": state, "last_error": last_error},
    )
    assert inserted is True
    assert event["event_uuid"] == event_uuid
    finish_run(
        run_id,
        status,
        summary=str(event["message"]),
        payload={"state": state, "last_error": last_error},
        scope=scope,
    )


def test_resolves_moved_v1_path_without_using_old_location(monkeypatch):
    scope = _scope()
    run_id = "monitor_history_v1"
    event_uuid = str(uuid4())
    manifest_path, chunk_paths, _manifest = _write_history(
        scope,
        run_id=run_id,
        manifest_updates={"sqlite_reconciled": True},
    )
    opened: list[Path] = []
    original_open = serial_monitor_store._open_readonly_no_reparse

    def recording_open(path: Path) -> int:
        opened.append(Path(path))
        return original_open(path)

    monkeypatch.setattr(
        serial_monitor_store,
        "_open_readonly_no_reparse",
        recording_open,
    )
    candidate = _resolve(
        scope,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert candidate.status == "resolved"
    assert candidate.adapter_id == ADAPTER_ID
    assert candidate.project_id == scope.project_id
    assert candidate.run_id == run_id
    assert candidate.requested_event_uuid == event_uuid
    assert candidate.manifest_format_version == 1
    assert candidate.manifest_sha256 == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    assert candidate.state == "STOPPED"
    assert candidate.terminal_at == normalize_timestamp(TERMINAL_AT)
    assert candidate.expected_run_status == "cancelled"
    assert candidate.expected_last_error is None
    assert len(candidate.artifacts.raw_logs) == 1
    assert candidate.artifacts.raw_logs[0].path == (
        f"serial/{run_id}/chunk-000001.bin"
    )
    assert candidate.artifacts.raw_logs[0].sha256 == hashlib.sha256(
        b"historical-monitor-chunk"
    ).hexdigest()
    assert candidate.artifacts.errors == ()
    assert set(opened) == {manifest_path, *chunk_paths}
    assert all("moved-project" not in str(path) for path in opened)


def test_resolves_moved_posix_v1_path_without_using_old_location(monkeypatch):
    scope = _scope()
    run_id = "monitor_history_posix_v1"
    manifest_path, chunk_paths, _manifest = _write_history(
        scope,
        run_id=run_id,
        legacy_paths=(
            f"/moved-project/logs/serial/{run_id}/chunk-000001.bin",
        ),
    )
    opened: list[Path] = []
    original_open = serial_monitor_store._open_readonly_no_reparse

    def recording_open(path: Path) -> int:
        opened.append(Path(path))
        return original_open(path)

    monkeypatch.setattr(
        serial_monitor_store,
        "_open_readonly_no_reparse",
        recording_open,
    )

    candidate = _resolve(
        scope,
        run_id=run_id,
        event_uuid=str(uuid4()),
    )

    assert candidate.status == "resolved"
    assert set(opened) == {manifest_path, *chunk_paths}
    assert all("moved-project" not in str(path) for path in opened)


def test_resolves_v2_and_reports_empty_history_explicitly():
    scope = _scope()
    event_uuid = str(uuid4())
    v2_run = "monitor_history_v2"
    _write_history(
        scope,
        run_id=v2_run,
        format_version=2,
    )
    v2 = _resolve(scope, run_id=v2_run, event_uuid=event_uuid)
    assert v2.status == "resolved"
    assert v2.manifest_format_version == 2
    assert v2.artifacts.raw_logs[0].path.endswith("chunk-000001.bin")

    empty_run = "monitor_history_empty"
    _write_history(scope, run_id=empty_run, payloads=())
    empty = _resolve(
        scope,
        run_id=empty_run,
        event_uuid=str(uuid4()),
    )
    assert empty.status == "no_artifacts"
    assert empty.artifacts.raw_logs == ()
    assert empty.artifacts.errors == ()
    assert len(empty.artifact_bundle_sha256) == 64


@pytest.mark.parametrize(
    "process_owner",
    [
        {
            "pid": 2_147_483_647,
            "process_token": "stale-owner-token",
            "process_started": "2000-01-01T00:00:00+00:00",
        },
        None,
    ],
)
def test_terminal_process_owner_is_inert_historical_metadata(process_owner):
    scope = _scope()
    run_id = f"monitor_history_stale_owner_{uuid4().hex[:8]}"
    manifest_path, _chunks, manifest = _write_history(
        scope,
        run_id=run_id,
    )
    if process_owner is None:
        manifest.pop("process_owner")
    else:
        manifest["process_owner"] = process_owner
    _replace_manifest(manifest_path, manifest)

    candidate = _resolve(
        scope,
        run_id=run_id,
        event_uuid=str(uuid4()),
    )

    assert candidate.status == "resolved"


@pytest.mark.parametrize("state", ["FAILED", "DISCONNECTED"])
def test_resolves_terminal_errors_and_does_not_share_mutable_manifest(
    state: str,
):
    scope = _scope()
    run_id = f"monitor_history_{state.lower()}"
    event_uuid = str(uuid4())
    last_error = {
        "error_kind": "serial_disconnected",
        "exception_type": "SerialException",
        "message": "device disconnected",
        "timestamp_utc": TERMINAL_AT,
    }
    detected_error = {
        "has_error": True,
        "error_kind": "micropython_traceback",
        "exception_type": "RuntimeError",
        "message": "board failed",
        "raw_text": "RuntimeError: board failed",
        "line": 12,
    }
    _write_history(
        scope,
        run_id=run_id,
        state=state,
        payloads=(),
        last_error=last_error,
        detected_error=detected_error,
    )
    candidate = _resolve(
        scope,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert candidate.status == "resolved"
    assert candidate.expected_run_status == "failed"
    assert candidate.expected_last_error == last_error
    exposed = candidate.expected_last_error
    try:
        exposed["message"] = "tampered"
    except TypeError:
        pass
    assert candidate.expected_last_error["message"] == "device disconnected"
    assert [error.occurrence_key for error in candidate.artifacts.errors] == [
        f"event:{event_uuid}:last_error",
        f"event:{event_uuid}:detected_error",
    ]


@pytest.mark.parametrize("event_uuid", [None, "", "   "])
def test_requires_explicit_event_uuid_before_normalization(event_uuid):
    scope = _scope()
    run_id = "monitor_history_uuid_required"
    _write_history(scope, run_id=run_id)
    with pytest.raises(Exception) as captured:
        _resolve(
            scope,
            run_id=run_id,
            event_uuid=event_uuid,
        )
    assert (
        getattr(captured.value, "error_kind", None)
        == "historical_event_uuid_required"
    )


def test_rejects_invalid_event_uuid_without_creating_database():
    scope = _scope()
    run_id = "monitor_history_bad_uuid"
    _write_history(scope, run_id=run_id)
    assert not scope.database_file.exists()
    _assert_resolution_error(
        scope,
        run_id=run_id,
        event_uuid="not-a-uuid",
    )
    assert not scope.database_file.exists()


@pytest.mark.parametrize(
    "run_id",
    [
        "..",
        "../outside",
        "..\\outside",
        "serial/outside",
        "serial\\outside",
        "C:\\outside",
        "/outside",
        "bad\0run",
    ],
)
def test_rejects_unsafe_caller_run_id_before_filesystem_access(run_id: str):
    scope = _scope()
    _assert_resolution_error(scope, run_id=run_id)
    assert not scope.log_root.exists()
    assert not scope.database_file.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        {"format_version": True},
        {"format_version": "1"},
        {"format_version": 3},
        {"project_id": "other-project"},
        {"run_id": "other-run"},
        {"state": "RUNNING"},
        {"stopped_at": "not-a-timestamp"},
        {"chunks": "not-a-list"},
        {"persisted_bytes": True},
    ],
)
def test_rejects_invalid_manifest_contract(mutation: dict):
    scope = _scope()
    run_id = f"monitor_history_manifest_{uuid4().hex[:8]}"
    manifest_path, _chunks, manifest = _write_history(
        scope,
        run_id=run_id,
    )
    manifest.update(mutation)
    _replace_manifest(manifest_path, manifest)
    _assert_resolution_error(scope, run_id=run_id)


def test_missing_format_version_is_legacy_v1():
    scope = _scope()
    run_id = "monitor_history_implicit_v1"
    _write_history(
        scope,
        run_id=run_id,
        include_format_version=False,
    )
    candidate = _resolve(
        scope,
        run_id=run_id,
        event_uuid=str(uuid4()),
    )
    assert candidate.manifest_format_version == 1


@pytest.mark.parametrize(
    "legacy_path",
    [
        "C:\\old\\logs\\serial\\wrong-run\\chunk-000001.bin",
        "C:\\old\\logs\\serial\\..\\monitor_history_bad_path\\chunk-000001.bin",
        "\\old\\serial\\monitor_history_bad_path\\chunk-000001.bin",
        "\\\\server\\share\\serial\\monitor_history_bad_path\\chunk-000001.bin",
        "\\\\?\\C:\\old\\serial\\monitor_history_bad_path\\chunk-000001.bin",
        "C:\\old\\serial\\monitor_history_bad_path\\other.bin",
        "C:\\old\\serial\\monitor_history_bad_path\\chunk-000001.bin\n",
    ],
)
def test_rejects_untrusted_v1_path_shapes(legacy_path: str):
    scope = _scope()
    run_id = "monitor_history_bad_path"
    _write_history(
        scope,
        run_id=run_id,
        legacy_paths=(legacy_path,),
    )
    _assert_resolution_error(scope, run_id=run_id)


def test_rejects_v2_path_and_mismatched_name():
    scope = _scope()
    for suffix, mutation in (
        ("path", {"path": "chunk-000001.bin"}),
        ("name", {"name": "chunk-000002.bin"}),
    ):
        run_id = f"monitor_history_v2_{suffix}"
        manifest_path, _chunks, manifest = _write_history(
            scope,
            run_id=run_id,
            format_version=2,
        )
        manifest["chunks"][0].update(mutation)
        _replace_manifest(manifest_path, manifest)
        _assert_resolution_error(scope, run_id=run_id)


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "noncontiguous",
        "bool_id",
        "length",
        "sha256",
        "persisted_bytes",
    ],
)
def test_rejects_inconsistent_chunk_metadata(case: str):
    scope = _scope()
    run_id = f"monitor_history_chunk_meta_{case}"
    payloads = (b"one", b"two") if case == "duplicate" else (b"one",)
    manifest_path, chunk_paths, manifest = _write_history(
        scope,
        run_id=run_id,
        payloads=payloads,
    )
    if case == "duplicate":
        manifest["chunks"][1]["chunk_id"] = 1
        manifest["chunks"][1]["path"] = manifest["chunks"][0]["path"]
    elif case == "noncontiguous":
        chunk_paths[0].rename(chunk_paths[0].with_name("chunk-000002.bin"))
        manifest["chunks"][0]["chunk_id"] = 2
        manifest["chunks"][0]["path"] = (
            "C:\\old\\logs\\serial\\"
            f"{run_id}\\chunk-000002.bin"
        )
    elif case == "bool_id":
        manifest["chunks"][0]["chunk_id"] = True
    elif case == "length":
        manifest["chunks"][0]["byte_length"] += 1
    elif case == "sha256":
        manifest["chunks"][0]["sha256"] = "0" * 64
    elif case == "persisted_bytes":
        manifest["persisted_bytes"] += 1
    _replace_manifest(manifest_path, manifest)
    _assert_resolution_error(scope, run_id=run_id)


@pytest.mark.parametrize("case", ["missing", "extra", "part"])
def test_rejects_inconsistent_chunk_file_set(case: str):
    scope = _scope()
    run_id = f"monitor_history_chunk_set_{case}"
    _manifest_path, chunk_paths, _manifest = _write_history(
        scope,
        run_id=run_id,
    )
    if case == "missing":
        chunk_paths[0].unlink()
    elif case == "extra":
        (chunk_paths[0].parent / "chunk-000002.bin").write_bytes(b"extra")
    else:
        (chunk_paths[0].parent / "chunk-000002.bin.part").write_bytes(b"part")
    _assert_resolution_error(scope, run_id=run_id)


def test_rejects_reparse_run_directory(tmp_path):
    scope = _scope()
    run_id = "monitor_history_reparse_run"
    serial_root = scope.log_root / "serial"
    serial_root.mkdir(parents=True)
    external = tmp_path / "external-run"
    external.mkdir()
    link = serial_root / run_id
    _directory_reparse(link, external)
    _assert_resolution_error(scope, run_id=run_id)


@pytest.mark.parametrize("ancestor", ["log_root", "serial_root"])
def test_rejects_reparse_ancestor_directory(tmp_path, ancestor: str):
    scope = _scope()
    run_id = f"monitor_history_reparse_{ancestor}"
    _write_history(scope, run_id=run_id)
    link = (
        scope.log_root
        if ancestor == "log_root"
        else scope.log_root / "serial"
    )
    external = tmp_path / f"external-{ancestor}"
    link.rename(external)
    _directory_reparse(link, external)

    _assert_resolution_error(scope, run_id=run_id)


def test_rejects_directory_identity_change_during_resolution(monkeypatch):
    scope = _scope()
    run_id = "monitor_history_directory_changed"
    _write_history(scope, run_id=run_id)
    original_identity = serial_monitor_backend.safe_directory_identity
    calls = 0

    def unstable_identity(path: Path, *, label: str, include_metadata: bool):
        nonlocal calls
        calls += 1
        identity = original_identity(
            path,
            label=label,
            include_metadata=include_metadata,
        )
        if calls == 8:
            return (*identity[:-1], identity[-1] + 1)
        return identity

    monkeypatch.setattr(
        serial_monitor_backend,
        "safe_directory_identity",
        unstable_identity,
    )
    _assert_resolution_error(scope, run_id=run_id)


def test_rejects_synthetic_fd_reparse_and_identity_change(monkeypatch):
    scope = _scope()
    first_run = "monitor_history_fd_reparse"
    _write_history(scope, run_id=first_run)
    original_fstat = serial_monitor_store.os.fstat

    def reparse_fstat(descriptor: int):
        current = original_fstat(descriptor)
        values = {
            name: getattr(current, name)
            for name in (
                "st_mode",
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
            )
        }
        values["st_file_attributes"] = 0x400
        return SimpleNamespace(**values)

    monkeypatch.setattr(serial_monitor_store.os, "fstat", reparse_fstat)
    _assert_resolution_error(scope, run_id=first_run)

    monkeypatch.setattr(serial_monitor_store.os, "fstat", original_fstat)
    second_run = "monitor_history_fd_changed"
    _write_history(scope, run_id=second_run)
    original_identity = serial_monitor_store._stat_identity
    regular_calls = 0

    def unstable_identity(status_value):
        nonlocal regular_calls
        identity = original_identity(status_value)
        if stat.S_ISREG(status_value.st_mode):
            regular_calls += 1
            if regular_calls % 2 == 0:
                return (*identity[:-1], identity[-1] + 1)
        return identity

    monkeypatch.setattr(
        serial_monitor_store,
        "_stat_identity",
        unstable_identity,
    )
    _assert_resolution_error(scope, run_id=second_run)


@pytest.mark.parametrize(
    "legacy_field",
    [
        "terminal_marker",
        "sqlite_artifact_projection",
        "sqlite_artifacts_reconciliation_error",
        "sqlite_artifacts_reconciliation_version",
    ],
)
def test_rejects_b3_sidecar_and_legacy_ownership(legacy_field: str):
    scope = _scope()
    run_id = f"monitor_history_owned_{legacy_field}"
    manifest_path, _chunks, manifest = _write_history(
        scope,
        run_id=run_id,
    )
    manifest[legacy_field] = {} if legacy_field != "sqlite_artifacts_reconciliation_version" else 1
    _replace_manifest(manifest_path, manifest)
    _assert_resolution_error(scope, run_id=run_id)

    sidecar_run = f"{run_id}_sidecar"
    sidecar_manifest, _chunks, _manifest = _write_history(
        scope,
        run_id=sidecar_run,
    )
    sidecar_manifest.with_name("sqlite-artifacts-v1.json").write_text(
        "{}",
        encoding="utf-8",
    )
    _assert_resolution_error(scope, run_id=sidecar_run)


def test_persistent_lease_file_is_not_b3_ownership():
    scope = _scope()
    run_id = "monitor_history_persistent_lease"
    manifest_path, _chunks, _manifest = _write_history(
        scope,
        run_id=run_id,
    )
    manifest_path.with_name(".sqlite-artifacts.lock").write_text(
        "released lease file remains on disk",
        encoding="utf-8",
    )

    candidate = _resolve(
        scope,
        run_id=run_id,
        event_uuid=str(uuid4()),
    )

    assert candidate.status == "resolved"


def test_resolver_is_file_only_and_has_zero_side_effects(monkeypatch):
    scope = _scope()
    run_id = "monitor_history_read_only"
    _write_history(scope, run_id=run_id)
    root_before = _tree_snapshot(scope.project_dir)
    assert not scope.database_file.exists()

    def forbidden_database_call(*_args, **_kwargs):
        raise AssertionError("B4.2 resolver must not access SQLite")

    monkeypatch.setattr(log_repository, "connect", forbidden_database_call)
    for name in ("get_run", "get_event", "get_run_events"):
        monkeypatch.setattr(
            log_repository,
            name,
            forbidden_database_call,
        )
    candidate = _resolve(
        scope,
        run_id=run_id,
        event_uuid=str(uuid4()),
    )

    assert candidate.status == "resolved"
    assert not scope.database_file.exists()
    assert _tree_snapshot(scope.project_dir) == root_before
    run_dir = _run_dir(scope, run_id)
    assert not (run_dir / ".sqlite-artifacts.lock").exists()
    assert not list(run_dir.glob("sqlite-artifacts-v*.json"))
    assert not list(run_dir.glob("*.tmp"))


def test_candidate_integrates_with_b41_retry_and_concurrency():
    scope = _scope()
    run_id = "monitor_history_b41"
    event_uuid = str(uuid4())
    last_error = {
        "error_kind": "serial_disconnected",
        "exception_type": "SerialException",
        "message": "device disconnected",
    }
    _write_history(
        scope,
        run_id=run_id,
        state="DISCONNECTED",
        last_error=last_error,
    )
    candidate = _resolve(
        scope,
        run_id=run_id,
        event_uuid=event_uuid,
    )
    _prepare_terminal_event(
        scope,
        run_id=run_id,
        event_uuid=event_uuid,
        status="failed",
        state="DISCONNECTED",
        last_error=last_error,
    )

    def reconcile() -> dict:
        return log_repository.reconcile_existing_event_artifacts(
            scope.database_file,
            project_id=scope.project_id,
            run_id=run_id,
            event_uuid=event_uuid,
            artifacts=candidate.artifacts,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(executor.map(lambda _index: reconcile(), range(2)))
    repeated = reconcile()

    for collection in ("raw_logs", "errors"):
        assert sum(
            int(item["inserted"])
            for report in reports
            for item in report[collection]
        ) == len(getattr(candidate.artifacts, collection))
        assert all(item["inserted"] is False for item in repeated[collection])
    run = log_repository.get_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
    )
    events = log_repository.get_run_events(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        tail=20,
    )
    assert run["status"] == "failed"
    assert run["next_sequence_no"] == 2
    assert len(events) == 1
    assert events[0]["event_uuid"] == event_uuid
