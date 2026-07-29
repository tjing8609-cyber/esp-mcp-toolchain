from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4, uuid5

import pytest

from esp_mcp_toolchain.backends import serial_monitor_store
from esp_mcp_toolchain.database import log_repository
from esp_mcp_toolchain.database.migrations import init_database
from esp_mcp_toolchain.tools.log_tools import LogScope


ADAPTER_MODULE = "esp_mcp_toolchain.backends.historical_capture_adapter"
ADAPTER_ID = "historical_serial_capture_jsonl_v1"
LEGACY_AT = "2026-07-12T14:14:39+08:00"
NATIVE_AT = "2026-07-22T15:16:57+00:00"


def _adapter():
    try:
        return importlib.import_module(ADAPTER_MODULE)
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"B4.3 adapter is missing: {exc}",
            pytrace=False,
        )


def _scope() -> LogScope:
    return LogScope.active()


def _legacy_uuid(project_id: str, run_id: str, event_id: str) -> str:
    return str(
        uuid5(
            log_repository.LEGACY_JSONL_NAMESPACE,
            f"event:{project_id}:{run_id}:{event_id}",
        )
    )


def _raw_path(raw_name: str, *, style: str = "windows") -> str:
    if style == "windows":
        return f"Z:\\moved-project\\logs\\raw\\{raw_name}"
    if style == "posix":
        return f"/moved-project/logs/raw/{raw_name}"
    raise AssertionError(style)


def _legacy_record(
    *,
    run_id: str,
    raw_name: str,
    event_id: str = "evt_historical_capture",
    raw_path: str | None = None,
    message: str = "Captured 707 characters from COM3",
) -> dict:
    return {
        "event_id": event_id,
        "run_id": run_id,
        "ts": LEGACY_AT,
        "tool": "esp_serial_capture",
        "level": "serial",
        "source": "esp32",
        "message": message,
        "data": {
            "port": "COM3",
            "baudrate": 115200,
            "duration_ms": 5000,
            "raw_path": raw_path or _raw_path(raw_name),
            "created_at": LEGACY_AT,
        },
    }


def _native_records(
    *,
    project_id: str,
    run_id: str,
    raw_name: str,
    event_uuid: str,
    raw_size: int,
    raw_path: str | None = None,
) -> list[dict]:
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
        "raw_path": raw_path or _raw_path(raw_name),
        "bytes_read": raw_size,
    }
    return [
        {
            "event_uuid": prepare_uuid,
            "event_id": prepare_uuid,
            "project_id": project_id,
            "run_id": run_id,
            "sequence_no": 1,
            "ts": "2026-07-22T15:16:56+00:00",
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
            "project_id": project_id,
            "run_id": run_id,
            "sequence_no": 2,
            "ts": NATIVE_AT,
            "phase": "complete",
            "level": "info",
            "tool": "esp_serial_capture",
            "source": "toolchain",
            "message": f"Captured {raw_size} characters from COM3.",
            "payload_json": complete_payload,
            "data": complete_payload,
            "deduplicated": False,
            "task_type": "serial_capture",
            "selected_port": "COM3",
        },
    ]


def _write_source(
    scope: LogScope,
    *,
    source_name: str,
    records: list[object],
    raw_name: str | None = None,
    raw_bytes: bytes = b"",
    separators: tuple[str, str] | None = None,
) -> tuple[Path, Path | None]:
    sessions = scope.log_root / "sessions"
    raw_root = scope.log_root / "raw"
    sessions.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    source_path = sessions / source_name
    lines = [
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=separators,
        )
        for record in records
    ]
    source_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    raw_path = None
    if raw_name is not None:
        raw_path = raw_root / raw_name
        raw_path.write_bytes(raw_bytes)
    return source_path, raw_path


def _resolve(
    scope: LogScope,
    *,
    source_name: str,
    run_id: str,
    event_uuid: str,
):
    return _adapter().resolve_historical_serial_capture_artifacts(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )


def _tree_snapshot(root: Path) -> dict[str, tuple]:
    if not root.exists():
        return {}
    snapshot: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        status = path.lstat()
        if path.is_symlink():
            snapshot[relative] = ("symlink", str(path.readlink()))
        elif path.is_dir():
            snapshot[relative] = (
                "dir",
                int(status.st_dev),
                int(status.st_ino),
                int(status.st_mtime_ns),
            )
        else:
            raw = path.read_bytes()
            snapshot[relative] = (
                "file",
                len(raw),
                hashlib.sha256(raw).hexdigest(),
                int(status.st_mtime_ns),
            )
    return snapshot


def test_legacy_capture_is_resolved_as_ineligible_text_evidence():
    scope = _scope()
    run_id = "serial_20260712_141434_b8ad9e27"
    source_name = f"{run_id}.jsonl"
    raw_name = "key_led_buzzer_reset_boot_20260712_141434.log"
    raw_bytes = "boot\r\n\uFFFDtrace\r\n".encode("utf-8")
    event_id = "evt_dd75b9fee6c54fe085f9dc8708affaae"
    event_uuid = _legacy_uuid(scope.project_id, run_id, event_id)
    source_path, _ = _write_source(
        scope,
        source_name=source_name,
        records=[
            _legacy_record(
                run_id=run_id,
                raw_name=raw_name,
                event_id=event_id,
            )
        ],
        raw_name=raw_name,
        raw_bytes=raw_bytes,
    )

    candidate = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert candidate.status == "ineligible"
    assert candidate.adapter_id == ADAPTER_ID
    assert candidate.reconciliation_version == 1
    assert candidate.source_format == "legacy_single_v1"
    assert candidate.project_id == scope.project_id
    assert candidate.run_id == run_id
    assert candidate.requested_event_uuid == event_uuid
    assert candidate.source_path == f"sessions/{source_name}"
    assert candidate.source_sha256 == hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert candidate.source_size == source_path.stat().st_size
    assert candidate.source_record_count == 1
    assert candidate.source_record_number == 1
    assert candidate.expected_event_phase == "unknown"
    assert candidate.expected_run_status == "succeeded"
    assert candidate.database_projection_eligible is False
    assert candidate.database_projection_reason == "legacy_event_phase_unknown"
    assert candidate.raw_size == len(raw_bytes)
    assert candidate.raw_bytes_exact is False
    assert candidate.artifact_content_kind == "legacy_utf8_replacement_text"
    assert len(candidate.artifacts.raw_logs) == 1
    assert candidate.artifacts.raw_logs[0].kind == "serial_capture_legacy_text"
    assert candidate.artifacts.raw_logs[0].path == f"raw/{raw_name}"
    assert candidate.artifacts.raw_logs[0].sha256 == hashlib.sha256(raw_bytes).hexdigest()
    assert candidate.artifacts.errors == ()
    assert len(candidate.source_record_sha256) == 64
    assert len(candidate.expected_event_profile_sha256) == 64
    assert len(candidate.artifact_bundle_sha256) == 64


def test_native_complete_with_modern_filename_is_projection_eligible_exact_raw():
    scope = _scope()
    run_id = "serial_capture_modern_history"
    source_name = "copied-native-session.jsonl"
    raw_name = "historical-native_20260728_120000_012345abcdef.log"
    raw_bytes = b"\xff\x00Traceback\r\n"
    event_uuid = str(uuid4())
    records = _native_records(
        project_id=scope.project_id,
        run_id=run_id,
        raw_name=raw_name,
        event_uuid=event_uuid,
        raw_size=len(raw_bytes),
    )
    _write_source(
        scope,
        source_name=source_name,
        records=records,
        raw_name=raw_name,
        raw_bytes=raw_bytes,
    )

    candidate = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert candidate.status == "resolved"
    assert candidate.source_format == "native_complete_v1"
    assert candidate.source_record_count == 2
    assert candidate.source_record_number == 2
    assert candidate.expected_event_phase == "complete"
    assert candidate.expected_run_status == "succeeded"
    assert candidate.database_projection_eligible is True
    assert candidate.database_projection_reason is None
    assert candidate.raw_bytes_exact is True
    assert candidate.artifact_content_kind == "exact_serial_bytes"
    assert candidate.artifacts.raw_logs[0].kind == "serial_capture_raw"
    assert candidate.artifacts.raw_logs[0].sha256 == hashlib.sha256(raw_bytes).hexdigest()


def test_native_candidate_profiles_connect_directly_to_strict_repository_gate():
    scope = _scope()
    run_id = "serial_capture_strict_profile"
    source_name = f"{run_id}.jsonl"
    raw_name = "strict-native_20260728_120000_012345abcdef.log"
    raw_bytes = b"strict historical bytes"
    event_uuid = str(uuid4())
    records = _native_records(
        project_id=scope.project_id,
        run_id=run_id,
        raw_name=raw_name,
        event_uuid=event_uuid,
        raw_size=len(raw_bytes),
    )
    _write_source(
        scope,
        source_name=source_name,
        records=records,
        raw_name=raw_name,
        raw_bytes=raw_bytes,
    )
    candidate = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )
    init_database(scope.database_file, project_id=scope.project_id)
    log_repository.create_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        task_type="serial_capture",
        started_at=records[0]["ts"],
        selected_port="COM3",
    )
    for record in records:
        _event, inserted = log_repository.append_event(
            scope.database_file,
            project_id=scope.project_id,
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
        assert inserted is True
    log_repository.finish_run(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        status="succeeded",
        ended_at=NATIVE_AT,
        summary=records[-1]["message"],
    )
    claims = tuple(
        log_repository.HistoricalRawClaim(
            path=artifact.path,
            kind=artifact.kind,
            sha256=str(artifact.sha256),
            adapter_id=candidate.adapter_id,
            reconciliation_version=candidate.reconciliation_version,
            event_profile_sha256=(
                candidate.expected_event_profile_sha256
            ),
            artifact_bundle_sha256=candidate.artifact_bundle_sha256,
        )
        for artifact in candidate.artifacts.raw_logs
    )

    report = log_repository.reconcile_existing_event_artifacts(
        scope.database_file,
        project_id=scope.project_id,
        run_id=run_id,
        event_uuid=event_uuid,
        artifacts=candidate.artifacts,
        expected_event_profile=candidate.expected_event_profile,
        expected_run_profile=candidate.expected_run_profile,
        expected_sequence_no=2,
        expected_next_sequence_no=3,
        raw_claims=claims,
    )

    assert [item["inserted"] for item in report["raw_claims"]] == [True]
    assert [item["inserted"] for item in report["raw_logs"]] == [True]


def test_native_complete_old_filename_remains_legacy_text_even_when_empty():
    scope = _scope()
    run_id = "serial_capture_20260722_231655_e3693940"
    source_name = f"{run_id}.jsonl"
    raw_name = "taskbook-readonly-firmware-probe_20260722_231655.log"
    event_uuid = str(uuid4())
    _write_source(
        scope,
        source_name=source_name,
        records=_native_records(
            project_id=scope.project_id,
            run_id=run_id,
            raw_name=raw_name,
            event_uuid=event_uuid,
            raw_size=0,
        ),
        raw_name=raw_name,
        raw_bytes=b"",
    )

    candidate = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert candidate.status == "resolved"
    assert candidate.database_projection_eligible is True
    assert candidate.raw_size == 0
    assert candidate.raw_bytes_exact is False
    assert candidate.artifacts.raw_logs[0].kind == "serial_capture_legacy_text"


@pytest.mark.parametrize("selected_port", ["COM3", None])
def test_native_failure_without_payload_port_remains_recoverable(
    selected_port: str | None,
):
    scope = _scope()
    run_id = f"serial_capture_failure_{selected_port or 'none'}"
    source_name = f"failure-{selected_port or 'none'}.jsonl"
    event_uuid = str(uuid4())
    records = _native_records(
        project_id=scope.project_id,
        run_id=run_id,
        raw_name="unused.log",
        event_uuid=event_uuid,
        raw_size=0,
    )
    for record in records:
        record["selected_port"] = selected_port
    completion_payload = {
        "error_kind": "serial_capture_failed",
        "bytes_read": 0,
        "failure_stage": "open",
    }
    records[-1]["level"] = "error"
    records[-1]["message"] = "Could not open the serial port."
    records[-1]["payload_json"] = completion_payload
    records[-1]["data"] = completion_payload
    _write_source(
        scope,
        source_name=source_name,
        records=records,
    )

    candidate = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert candidate.status == "resolved"
    assert candidate.expected_run_status == "failed"
    assert candidate.expected_run_profile["selected_port"] == selected_port
    assert candidate.artifacts.raw_logs == ()
    assert len(candidate.artifacts.errors) == 1
    assert candidate.artifacts.errors[0].error_kind == "serial_capture_failed"


def test_legacy_jsonl_cannot_claim_exact_raw_with_a_modern_looking_filename():
    scope = _scope()
    run_id = "serial_capture_legacy_modern_name"
    source_name = "legacy-modern-name.jsonl"
    raw_name = "renamed_20260728_120000_012345abcdef.log"
    event_id = "evt_legacy_modern_name"
    event_uuid = _legacy_uuid(scope.project_id, run_id, event_id)
    _write_source(
        scope,
        source_name=source_name,
        records=[
            _legacy_record(
                run_id=run_id,
                raw_name=raw_name,
                event_id=event_id,
            )
        ],
        raw_name=raw_name,
        raw_bytes="\uFFFD".encode("utf-8"),
    )

    candidate = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert candidate.raw_bytes_exact is False
    assert candidate.artifact_content_kind == "legacy_utf8_replacement_text"
    assert candidate.artifacts.raw_logs[0].kind == "serial_capture_legacy_text"


@pytest.mark.parametrize("style", ["windows", "posix"])
def test_moved_absolute_path_is_only_identity_metadata(style: str):
    scope = _scope()
    run_id = f"serial_capture_moved_{style}"
    source_name = f"alias-{style}.jsonl"
    raw_name = "moved_20260728_120000_012345abcdef.log"
    raw_bytes = b"moved capture"
    event_uuid = str(uuid4())
    _write_source(
        scope,
        source_name=source_name,
        records=_native_records(
            project_id=scope.project_id,
            run_id=run_id,
            raw_name=raw_name,
            raw_path=_raw_path(raw_name, style=style),
            event_uuid=event_uuid,
            raw_size=len(raw_bytes),
        ),
        raw_name=raw_name,
        raw_bytes=raw_bytes,
    )

    candidate = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert candidate.artifacts.raw_logs[0].path == f"raw/{raw_name}"


def test_source_alias_does_not_change_event_or_artifact_identity():
    scope = _scope()
    run_id = "serial_capture_alias"
    raw_name = "alias_20260728_120000_012345abcdef.log"
    raw_bytes = b"alias"
    event_uuid = str(uuid4())
    records = _native_records(
        project_id=scope.project_id,
        run_id=run_id,
        raw_name=raw_name,
        event_uuid=event_uuid,
        raw_size=len(raw_bytes),
    )
    first_source, _ = _write_source(
        scope,
        source_name="first-alias.jsonl",
        records=records,
        raw_name=raw_name,
        raw_bytes=raw_bytes,
        separators=(",", ":"),
    )
    second_source, _ = _write_source(
        scope,
        source_name="second-alias.jsonl",
        records=records,
        separators=(", ", ": "),
    )

    first = _resolve(
        scope,
        source_name=first_source.name,
        run_id=run_id,
        event_uuid=event_uuid,
    )
    second = _resolve(
        scope,
        source_name=second_source.name,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert first.source_sha256 != second.source_sha256
    assert first.source_record_sha256 != second.source_record_sha256
    assert first.expected_event_profile_sha256 == second.expected_event_profile_sha256
    assert first.artifact_bundle_sha256 == second.artifact_bundle_sha256
    assert first.artifacts == second.artifacts


def test_candidate_profiles_are_deeply_immutable_snapshots():
    scope = _scope()
    run_id = "serial_capture_immutable"
    source_name = "immutable.jsonl"
    raw_name = "immutable_20260728_120000_012345abcdef.log"
    event_uuid = str(uuid4())
    _write_source(
        scope,
        source_name=source_name,
        records=_native_records(
            project_id=scope.project_id,
            run_id=run_id,
            raw_name=raw_name,
            event_uuid=event_uuid,
            raw_size=1,
        ),
        raw_name=raw_name,
        raw_bytes=b"x",
    )
    candidate = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    event_profile = candidate.expected_event_profile
    run_profile = candidate.expected_run_profile
    event_profile["payload_json"]["raw_path"] = "tampered"
    run_profile["status"] = "tampered"

    assert candidate.expected_event_profile["payload_json"]["raw_path"] != "tampered"
    assert candidate.expected_run_profile["status"] == "succeeded"


def test_same_event_uuid_with_changed_profile_or_raw_changes_bound_digest():
    scope = _scope()
    run_id = "serial_capture_profile_binding"
    raw_name = "profile_20260728_120000_012345abcdef.log"
    raw_bytes = b"first"
    event_uuid = str(uuid4())
    first_records = _native_records(
        project_id=scope.project_id,
        run_id=run_id,
        raw_name=raw_name,
        event_uuid=event_uuid,
        raw_size=len(raw_bytes),
    )
    _write_source(
        scope,
        source_name="profile-first.jsonl",
        records=first_records,
        raw_name=raw_name,
        raw_bytes=raw_bytes,
    )
    first = _resolve(
        scope,
        source_name="profile-first.jsonl",
        run_id=run_id,
        event_uuid=event_uuid,
    )

    second_records = json.loads(json.dumps(first_records))
    second_records[-1]["message"] = "same UUID, changed message"
    second_records[-1]["payload_json"]["bytes_read"] = len(b"second")
    second_records[-1]["data"]["bytes_read"] = len(b"second")
    (scope.log_root / "raw" / raw_name).write_bytes(b"second")
    _write_source(
        scope,
        source_name="profile-second.jsonl",
        records=second_records,
    )
    second = _resolve(
        scope,
        source_name="profile-second.jsonl",
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert first.expected_event_profile_sha256 != second.expected_event_profile_sha256
    assert first.artifact_bundle_sha256 != second.artifact_bundle_sha256


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "relative/raw/capture.log",
        "\\root-relative\\logs\\raw\\capture.log",
        "\\\\server\\share\\logs\\raw\\capture.log",
        "\\\\?\\C:\\old\\logs\\raw\\capture.log",
        "C:drive-relative\\logs\\raw\\capture.log",
        "C:\\old\\logs\\raw\\..\\raw\\capture.log",
        "C:\\old\\logs\\raw\\capture.log\n",
        "/old/logs/raw/../raw/capture.log",
        "//server/share/logs/raw/capture.log",
        "/old/logs/not-raw/capture.log",
    ],
)
def test_rejects_untrusted_legacy_raw_path_shapes(unsafe_path: str):
    scope = _scope()
    run_id = "serial_capture_unsafe_path"
    source_name = f"unsafe-{uuid4().hex}.jsonl"
    raw_name = "capture.log"
    event_id = f"evt_{uuid4().hex}"
    event_uuid = _legacy_uuid(scope.project_id, run_id, event_id)
    _write_source(
        scope,
        source_name=source_name,
        records=[
            _legacy_record(
                run_id=run_id,
                raw_name=raw_name,
                event_id=event_id,
                raw_path=unsafe_path,
            )
        ],
        raw_name=raw_name,
        raw_bytes=b"capture",
    )

    with pytest.raises(Exception) as captured:
        _resolve(
            scope,
            source_name=source_name,
            run_id=run_id,
            event_uuid=event_uuid,
        )

    assert (
        getattr(captured.value, "error_kind", None)
        == "historical_capture_resolution_failed"
    )


@pytest.mark.parametrize(
    "source_name,run_id",
    [
        ("../outside.jsonl", "safe-run"),
        ("nested/outside.jsonl", "safe-run"),
        ("C:\\outside.jsonl", "safe-run"),
        ("capture.txt", "safe-run"),
        ("safe.jsonl", "../outside"),
        ("safe.jsonl", "nested/outside"),
        ("safe.jsonl", "bad\0run"),
    ],
)
def test_rejects_unsafe_source_or_run_before_database_creation(
    source_name: str,
    run_id: str,
):
    scope = _scope()
    with pytest.raises(Exception):
        _resolve(
            scope,
            source_name=source_name,
            run_id=run_id,
            event_uuid=str(uuid4()),
        )
    assert not scope.database_file.exists()


def test_rejects_invalid_or_mismatched_requested_event_uuid():
    scope = _scope()
    run_id = "serial_capture_uuid"
    source_name = "uuid.jsonl"
    raw_name = "uuid.log"
    event_id = "evt_uuid_contract"
    expected_uuid = _legacy_uuid(scope.project_id, run_id, event_id)
    _write_source(
        scope,
        source_name=source_name,
        records=[
            _legacy_record(
                run_id=run_id,
                raw_name=raw_name,
                event_id=event_id,
            )
        ],
        raw_name=raw_name,
        raw_bytes=b"uuid",
    )

    for requested in ("not-a-uuid", str(uuid4())):
        with pytest.raises(Exception):
            _resolve(
                scope,
                source_name=source_name,
                run_id=run_id,
                event_uuid=requested,
            )

    assert expected_uuid != "not-a-uuid"
    assert not scope.database_file.exists()


@pytest.mark.parametrize(
    "case",
    ["partial", "invalid_utf8", "non_object", "oversized"],
)
def test_rejects_malformed_non_object_or_oversized_jsonl(case: str):
    scope = _scope()
    sessions = scope.log_root / "sessions"
    raw_root = scope.log_root / "raw"
    sessions.mkdir(parents=True)
    raw_root.mkdir()
    source_name = f"bad-{uuid4().hex}.jsonl"
    contents = {
        "partial": b'{"partial":',
        "invalid_utf8": b"\xff\n",
        "non_object": b"[]\n",
        "oversized": b'{"value":"' + b"x" * (256 * 1024) + b'"}\n',
    }[case]
    (sessions / source_name).write_bytes(contents)

    with pytest.raises(Exception):
        _resolve(
            scope,
            source_name=source_name,
            run_id="serial_capture_bad_jsonl",
            event_uuid=str(uuid4()),
        )

    assert not scope.database_file.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "run",
        "project",
        "payload_mirror",
        "non_terminal",
        "not_last",
        "duplicate",
        "duplicate_event_uuid",
        "first_phase",
        "intermediate_execute",
        "task_type",
        "source",
        "selected_port",
        "missing_selected_port",
        "payload_port",
    ],
)
def test_rejects_ambiguous_or_conflicting_native_profile(mutation: str):
    scope = _scope()
    run_id = f"serial_capture_profile_{mutation}"
    source_name = f"{mutation}.jsonl"
    raw_name = f"{mutation}_20260728_120000_012345abcdef.log"
    raw_bytes = b"profile"
    event_uuid = str(uuid4())
    records = _native_records(
        project_id=scope.project_id,
        run_id=run_id,
        raw_name=raw_name,
        event_uuid=event_uuid,
        raw_size=len(raw_bytes),
    )
    if mutation == "run":
        records[-1]["run_id"] = "other-run"
    elif mutation == "project":
        records[-1]["project_id"] = "other-project"
    elif mutation == "payload_mirror":
        records[-1]["data"] = {"raw_path": records[-1]["data"]["raw_path"]}
    elif mutation == "non_terminal":
        records[-1]["phase"] = "execute"
    elif mutation == "not_last":
        records.append(
            {
                **records[-1],
                "event_uuid": str(uuid4()),
                "event_id": str(uuid4()),
                "sequence_no": 3,
                "phase": "verify",
            }
        )
    elif mutation == "duplicate":
        duplicate = json.loads(json.dumps(records[-1]))
        duplicate["event_uuid"] = str(uuid4())
        duplicate["event_id"] = duplicate["event_uuid"]
        duplicate["sequence_no"] = 3
        records.append(duplicate)
    elif mutation == "duplicate_event_uuid":
        records[0]["event_uuid"] = event_uuid
        records[0]["event_id"] = event_uuid
    elif mutation == "first_phase":
        records[0]["phase"] = "verify"
    elif mutation == "intermediate_execute":
        intermediate = json.loads(json.dumps(records[0]))
        intermediate["event_uuid"] = str(uuid4())
        intermediate["event_id"] = intermediate["event_uuid"]
        intermediate["sequence_no"] = 2
        intermediate["phase"] = "execute"
        records[-1]["sequence_no"] = 3
        records.insert(1, intermediate)
    elif mutation == "task_type":
        records[0]["task_type"] = "other_task"
    elif mutation == "source":
        records[0]["source"] = "other_source"
    elif mutation == "selected_port":
        records[0]["selected_port"] = "COM4"
    elif mutation == "missing_selected_port":
        del records[0]["selected_port"]
    else:
        records[-1]["payload_json"]["port"] = "COM4"
        records[-1]["data"]["port"] = "COM4"
    _write_source(
        scope,
        source_name=source_name,
        records=records,
        raw_name=raw_name,
        raw_bytes=raw_bytes,
    )

    with pytest.raises(Exception):
        _resolve(
            scope,
            source_name=source_name,
            run_id=run_id,
            event_uuid=event_uuid,
        )


@pytest.mark.parametrize("failure", ["missing", "size_mismatch"])
def test_claimed_capture_requires_current_file_and_exact_native_size(failure: str):
    scope = _scope()
    run_id = f"serial_capture_raw_{failure}"
    source_name = f"{failure}.jsonl"
    raw_name = f"{failure}_20260728_120000_012345abcdef.log"
    event_uuid = str(uuid4())
    records = _native_records(
        project_id=scope.project_id,
        run_id=run_id,
        raw_name=raw_name,
        event_uuid=event_uuid,
        raw_size=8,
    )
    _write_source(
        scope,
        source_name=source_name,
        records=records,
        raw_name=None if failure == "missing" else raw_name,
        raw_bytes=b"short",
    )

    with pytest.raises(Exception):
        _resolve(
            scope,
            source_name=source_name,
            run_id=run_id,
            event_uuid=event_uuid,
        )


def test_resolution_has_no_database_lease_marker_or_file_write_side_effect(
    monkeypatch,
):
    scope = _scope()
    run_id = "serial_capture_read_only"
    source_name = "read-only.jsonl"
    raw_name = "read-only_20260728_120000_012345abcdef.log"
    raw_bytes = b"read only"
    event_uuid = str(uuid4())
    _write_source(
        scope,
        source_name=source_name,
        records=_native_records(
            project_id=scope.project_id,
            run_id=run_id,
            raw_name=raw_name,
            event_uuid=event_uuid,
            raw_size=len(raw_bytes),
        ),
        raw_name=raw_name,
        raw_bytes=raw_bytes,
    )
    before = _tree_snapshot(scope.project_dir)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only adapter called a forbidden mutating API")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(log_repository, "import_jsonl_sessions", forbidden)
    monkeypatch.setattr(
        log_repository,
        "reconcile_existing_event_artifacts",
        forbidden,
    )
    monkeypatch.setattr(
        serial_monitor_store.SerialRunReconciliationLease,
        "acquire",
        forbidden,
    )

    first = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )
    second = _resolve(
        scope,
        source_name=source_name,
        run_id=run_id,
        event_uuid=event_uuid,
    )

    assert first == second
    assert _tree_snapshot(scope.project_dir) == before
    assert not scope.database_file.exists()


def test_reparse_source_or_raw_is_refused(tmp_path):
    scope = _scope()
    sessions = scope.log_root / "sessions"
    raw_root = scope.log_root / "raw"
    sessions.mkdir(parents=True)
    raw_root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    target_source = external / "capture.jsonl"
    target_raw = external / "capture.log"
    target_source.write_text("{}\n", encoding="utf-8")
    target_raw.write_bytes(b"capture")
    source_link = sessions / "source-link.jsonl"
    raw_link = raw_root / "raw-link.log"
    try:
        source_link.symlink_to(target_source)
        raw_link.symlink_to(target_raw)
    except OSError:
        pytest.skip("This Windows account cannot create test symlinks.")

    for source_name, run_id, event_uuid in [
        (source_link.name, "serial_capture_source_link", str(uuid4())),
    ]:
        with pytest.raises(Exception):
            _resolve(
                scope,
                source_name=source_name,
                run_id=run_id,
                event_uuid=event_uuid,
            )

    run_id = "serial_capture_raw_link"
    event_uuid = str(uuid4())
    records = _native_records(
        project_id=scope.project_id,
        run_id=run_id,
        raw_name=raw_link.name,
        event_uuid=event_uuid,
        raw_size=len(b"capture"),
    )
    (sessions / "raw-link.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        _resolve(
            scope,
            source_name="raw-link.jsonl",
            run_id=run_id,
            event_uuid=event_uuid,
        )
