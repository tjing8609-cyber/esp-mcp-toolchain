from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import time
from types import SimpleNamespace

from esp_mcp_toolchain.backends import raw_repl_backend
from esp_mcp_toolchain.backends.serial_monitor_backend import SERIAL_MONITOR_MANAGER
from esp_mcp_toolchain.tools import error_tools, exec_tools, log_tools, serial_tools
from esp_mcp_toolchain.utils.error_detection import MicroPythonErrorDetector


class InterruptSerial:
    instances: list["InterruptSerial"] = []

    def __init__(self, port: str | None = None, baudrate: int = 115200, timeout: float = 0.1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = None
        self.rtscts = True
        self.dsrdtr = True
        self.xonxoff = True
        self.dtr = True
        self.rts = True
        self.writes: list[bytes] = []
        self.closed = False
        self.opened = False
        self.open_snapshot: dict[str, object] = {}
        self._reads: list[bytes] = []
        type(self).instances.append(self)

    def open(self) -> None:
        self.open_snapshot = {
            "port": self.port,
            "baudrate": self.baudrate,
            "timeout": self.timeout,
            "write_timeout": self.write_timeout,
            "rtscts": self.rtscts,
            "dsrdtr": self.dsrdtr,
            "xonxoff": self.xonxoff,
            "dtr": self.dtr,
            "rts": self.rts,
        }
        self.opened = True

    def reset_input_buffer(self) -> None:
        self._reads.clear()

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        if self.writes.count(b"\x03") == 2:
            self._reads.append(b"Traceback\r\nKeyboardInterrupt\r\n>>>")
        return len(data)

    def read(self, _size: int) -> bytes:
        return self._reads.pop(0) if self._reads else b""

    def close(self) -> None:
        self.closed = True


class InterruptSerialModule:
    Serial = InterruptSerial


def test_interrupt_program_sends_only_ctrl_c_and_issues_no_reset_command(monkeypatch):
    InterruptSerial.instances = []
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: InterruptSerialModule)

    result = raw_repl_backend.interrupt_program("COM_TEST", timeout_ms=200)

    serial = InterruptSerial.instances[0]
    assert result["ok"] is True
    assert result["stop_confirmed"] is True
    assert result["observed_keyboard_interrupt"] is True
    assert result["observed_prompt"] is True
    assert result["reset_command_sent"] is False
    assert result["physical_reset_excluded"] is False
    assert serial.writes == [b"\x03", b"\x03"]
    assert b"\x04" not in serial.writes
    assert serial.dtr is False
    assert serial.rts is False
    assert serial.closed is True
    assert serial.open_snapshot == {
        "port": "COM_TEST",
        "baudrate": 115200,
        "timeout": 0.1,
        "write_timeout": 1.0,
        "rtscts": False,
        "dsrdtr": False,
        "xonxoff": False,
        "dtr": False,
        "rts": False,
    }


def test_interrupt_program_short_write_is_not_reported_as_sent(monkeypatch):
    class ShortWriteSerial(InterruptSerial):
        def write(self, data: bytes) -> int:
            self.writes.append(data)
            return 0

    class ShortWriteSerialModule:
        Serial = ShortWriteSerial

    monkeypatch.setattr(
        raw_repl_backend,
        "get_serial_module",
        lambda: ShortWriteSerialModule,
    )

    result = raw_repl_backend.interrupt_program(
        "COM_TEST",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "program_stop_io_error"
    assert result["failure_stage"] == "interrupt_write"
    assert result["interrupt_sent"] is False
    assert result["interrupt_write_count"] == 0
    assert result["stop_confirmed"] is False
    assert ShortWriteSerial.instances[0].closed is True


def test_interrupt_program_counts_only_complete_ctrl_c_writes(monkeypatch):
    class SecondShortWriteSerial(InterruptSerial):
        def write(self, data: bytes) -> int:
            self.writes.append(data)
            if len(self.writes) == 2:
                return 0
            return len(data)

    class SecondShortWriteSerialModule:
        Serial = SecondShortWriteSerial

    monkeypatch.setattr(
        raw_repl_backend,
        "get_serial_module",
        lambda: SecondShortWriteSerialModule,
    )

    result = raw_repl_backend.interrupt_program(
        "COM_TEST",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "program_stop_io_error"
    assert result["failure_stage"] == "interrupt_write"
    assert result["interrupt_sent"] is True
    assert result["interrupt_write_count"] == 1
    assert result["stop_confirmed"] is False
    assert SecondShortWriteSerial.instances[0].closed is True


def test_program_stop_adds_auditable_metadata(monkeypatch):
    monkeypatch.setattr(
        exec_tools,
        "interrupt_program",
        lambda port, baudrate, timeout_ms: {
            "ok": True,
            "interrupt_sent": True,
            "stop_confirmed": True,
            "message": f"stopped {port}",
        },
    )

    result = exec_tools.esp_program_stop("COM_TEST", timeout_ms=700)

    assert result["ok"] is True
    assert result["tool_name"] == "esp_program_stop"
    assert result["implemented"] is True
    assert result["port"] == "COM_TEST"
    assert result["reset_command_sent"] is False
    assert result["physical_reset_excluded"] is False


def test_interrupt_program_reports_unconfirmed_without_claiming_stop(monkeypatch):
    class SilentSerial(InterruptSerial):
        def write(self, data: bytes) -> int:
            self.writes.append(data)
            return len(data)

    class SilentSerialModule:
        Serial = SilentSerial

    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: SilentSerialModule)

    result = raw_repl_backend.interrupt_program("COM_TEST", timeout_ms=100)

    assert result["ok"] is False
    assert result["error_kind"] == "program_stop_unconfirmed"
    assert result["interrupt_sent"] is True
    assert result["stop_confirmed"] is False



class CaptureSerial:
    chunks: list[bytes] = []
    instances: list["CaptureSerial"] = []

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
        self.open_snapshot: dict[str, object] = {}
        self.closed = False
        self._chunks = list(type(self).chunks)
        type(self).instances.append(self)

    def open(self) -> None:
        self.open_snapshot = {
            "port": self.port,
            "baudrate": self.baudrate,
            "timeout": self.timeout,
            "write_timeout": self.write_timeout,
            "rtscts": self.rtscts,
            "dsrdtr": self.dsrdtr,
            "xonxoff": self.xonxoff,
            "dtr": self.dtr,
            "rts": self.rts,
        }

    def read(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def close(self) -> None:
        self.closed = True


class CaptureSerialModule:
    Serial = CaptureSerial


def test_incremental_detector_completes_traceback_split_across_chunks():
    detector = MicroPythonErrorDetector()

    assert detector.feed("Trace") is None
    partial = detector.feed(
        'back (most recent call last):\n  File "main.py", line 12, in <module>\n'
    )
    assert partial is not None
    assert partial["exception_type"] is None

    complete = detector.feed("NameError: name PWM is not defined\n")

    assert complete["has_error"] is True
    assert complete["error_kind"] == "micropython_traceback"
    assert complete["file"] == "main.py"
    assert complete["line"] == 12
    assert complete["exception_type"] == "NameError"


def test_serial_capture_returns_structured_error_report_across_reads(monkeypatch):
    CaptureSerial.instances = []
    CaptureSerial.chunks = [
        b"Trace",
        b'back (most recent call last):\r\n  File "boot.py", line 7\r\n',
        b"ValueError: bad pin\r\n",
    ]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=1000,
        stop_on_traceback=True,
        session_name="error-split",
    )

    assert result["ok"] is True
    assert result["has_error"] is True
    assert result["error_report"]["exception_type"] == "ValueError"
    assert result["error_report"]["file"] == "boot.py"
    assert Path(result["raw_path"]).is_file()
    assert CaptureSerial.instances[0].open_snapshot["dtr"] is False
    assert CaptureSerial.instances[0].open_snapshot["rts"] is False
    assert CaptureSerial.instances[0].closed is True


def test_serial_capture_preserves_non_utf8_raw_bytes(monkeypatch):
    CaptureSerial.instances = []
    CaptureSerial.chunks = [b"\xff\x00boot\r\n", b"\x80done\r\n"]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=False,
        session_name="binary-raw",
    )

    expected = b"\xff\x00boot\r\n\x80done\r\n"
    assert result["ok"] is True
    assert result["bytes_read"] == len(expected)
    assert Path(result["raw_path"]).read_bytes() == expected
    assert "\ufffd" in result["text"]


def test_serial_capture_exclusive_create_retries_uuid_collision_without_overwrite(monkeypatch):
    CaptureSerial.instances = []
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)
    monkeypatch.setattr(serial_tools, "now_compact", lambda: "20260727_120000")
    uuids = iter(
        [
            SimpleNamespace(hex="a" * 32),
            SimpleNamespace(hex="b" * 32),
        ]
    )
    monkeypatch.setattr(serial_tools, "uuid4", lambda: next(uuids))
    raw_dir = serial_tools.logs_dir() / "raw"
    raw_dir.mkdir(parents=True)
    sentinel = raw_dir / "same-second_20260727_120000_aaaaaaaaaaaa.log"
    sentinel.write_bytes(b"sentinel")

    CaptureSerial.chunks = [b"new capture"]
    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=False,
        session_name="same-second",
    )

    result_path = Path(result["raw_path"])
    assert sentinel.read_bytes() == b"sentinel"
    assert result_path.name == "same-second_20260727_120000_bbbbbbbbbbbb.log"
    assert result_path.read_bytes() == b"new capture"


def test_serial_capture_fsync_failure_returns_recovery_path(monkeypatch):
    CaptureSerial.instances = []
    CaptureSerial.chunks = [b"persist me"]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)
    monkeypatch.setattr(
        serial_tools.os,
        "fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("forced fsync failure")),
    )

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=False,
        session_name="fsync-failure",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "serial_capture_persist_failed"
    assert result["failure_stage"] == "fsync"
    assert result["bytes_read"] == len(b"persist me")
    assert result["text"] == "persist me"
    recovery_path = Path(result["recovery_path"])
    assert recovery_path.is_file()
    assert recovery_path.read_bytes() == b"persist me"
    assert result["cleanup_completed"] is True
    assert result["persistence_cleanup_completed"] is True
    completion = log_tools.esp_logs_get(result["run_id"])["events"][-1]
    assert completion["payload_json"]["recovery_path"] == str(recovery_path)
    assert "text" not in completion["payload_json"]


def test_serial_capture_collision_exhaustion_never_claims_existing_file_as_recovery(
    monkeypatch,
):
    CaptureSerial.instances = []
    CaptureSerial.chunks = [b"new capture"]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)
    monkeypatch.setattr(serial_tools, "now_compact", lambda: "20260727_120000")
    monkeypatch.setattr(
        serial_tools,
        "uuid4",
        lambda: SimpleNamespace(hex="c" * 32),
    )
    raw_dir = serial_tools.logs_dir() / "raw"
    raw_dir.mkdir(parents=True)
    sentinel = raw_dir / "collision_20260727_120000_cccccccccccc.log"
    sentinel.write_bytes(b"belongs to an earlier run")

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=False,
        session_name="collision",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "serial_capture_persist_failed"
    assert result["failure_stage"] == "allocate"
    assert "recovery_path" not in result
    assert sentinel.read_bytes() == b"belongs to an earlier run"


def test_serial_capture_close_failure_reports_persistence_cleanup_gap(monkeypatch):
    class CloseFailingHandle:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def write(self, payload: bytes) -> int:
            return self._wrapped.write(payload)

        def flush(self) -> None:
            self._wrapped.flush()

        def fileno(self) -> int:
            return self._wrapped.fileno()

        def close(self) -> None:
            self._wrapped.close()
            raise OSError("forced close failure")

    original_open = Path.open

    def close_failing_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return CloseFailingHandle(handle) if mode == "xb" else handle

    CaptureSerial.instances = []
    CaptureSerial.chunks = [b"close me"]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)
    monkeypatch.setattr(Path, "open", close_failing_open)

    result = serial_tools.esp_serial_capture.__wrapped__(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=False,
        session_name="close-failure",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "serial_capture_persist_failed"
    assert result["failure_stage"] == "close"
    assert result["cleanup_completed"] is True
    assert result["persistence_cleanup_completed"] is False
    assert "forced close failure" in result["persistence_close_error"]
    recovery_path = Path(result["recovery_path"])
    assert recovery_path.read_bytes() == b"close me"


def test_serial_capture_prepares_raw_directory_before_opening_port(monkeypatch, tmp_path):
    CaptureSerial.instances = []
    CaptureSerial.chunks = [b"must not be read"]
    blocked_root = tmp_path / "not-a-directory"
    blocked_root.write_text("blocked", encoding="utf-8")
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: CaptureSerialModule)
    monkeypatch.setattr(serial_tools, "logs_dir", lambda: blocked_root)

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=20,
        stop_on_traceback=False,
        session_name="prepare-failure",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "serial_capture_persist_failed"
    assert result["failure_stage"] == "prepare_log_directory"
    assert result["bytes_read"] == 0
    assert result["physical_reset_excluded"] is True
    assert CaptureSerial.instances == []


def test_serial_capture_read_failure_keeps_original_byte_count(monkeypatch):
    class FailingReadSerial(CaptureSerial):
        def read(self, _size: int) -> bytes:
            if self._chunks:
                return self._chunks.pop(0)
            raise OSError("forced read failure")

    class FailingReadSerialModule:
        Serial = FailingReadSerial

    FailingReadSerial.instances = []
    FailingReadSerial.chunks = [b"\xff\x80"]
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: FailingReadSerialModule)

    result = serial_tools.esp_serial_capture(
        port="COM_CAPTURE",
        duration_ms=100,
        stop_on_traceback=False,
        session_name="read-failure",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "serial_capture_failed"
    assert result["bytes_read"] == 2
    assert result["text"] == "\ufffd\ufffd"
    assert result["cleanup_completed"] is True
    assert FailingReadSerial.instances[0].closed is True


class MonitorSerial:
    queue: Queue = Queue()

    def __init__(self):
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.rtscts = True
        self.dsrdtr = True
        self.xonxoff = True
        self.dtr = True
        self.rts = True
        self.closed = False

    def open(self):
        return None

    def read(self, _size: int) -> bytes:
        if self.closed:
            return b""
        try:
            return type(self).queue.get(timeout=0.02)
        except Empty:
            return b""

    def cancel_read(self):
        type(self).queue.put(b"")

    def close(self):
        self.closed = True


class MonitorSerialModule:
    Serial = MonitorSerial


def _monitor_identity(port: str) -> dict:
    return {
        "port": port,
        "device_path": port,
        "vid": "FFFF",
        "pid": "0001",
        "serial_number": port,
        "location": "test",
    }


def test_background_monitor_detects_once_and_parse_log_scans_persisted_raw(monkeypatch):
    SERIAL_MONITOR_MANAGER.shutdown_all(1)
    MonitorSerial.queue = Queue()
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: MonitorSerialModule)
    monkeypatch.setattr(serial_tools, "describe_serial_port", _monitor_identity)

    start = serial_tools.esp_serial_monitor_start("COM_MONITOR_ERROR", session_name="error-monitor")
    run_id = start["run_id"]
    MonitorSerial.queue.put(b"Trace")
    MonitorSerial.queue.put(
        b'back (most recent call last):\r\n  File "main.py", line 21, in task\r\n'
    )
    MonitorSerial.queue.put(b"RuntimeError: buzzer failed\r\n")

    deadline = time.monotonic() + 3
    detected = None
    while time.monotonic() < deadline:
        status = serial_tools.esp_serial_monitor_status(run_id)["monitors"][0]
        detected = status.get("detected_error")
        if detected and detected.get("exception_type") == "RuntimeError":
            break
        time.sleep(0.01)

    assert detected is not None
    assert detected["file"] == "main.py"
    read = serial_tools.esp_serial_monitor_read(run_id, wait_ms=1000)
    assert read["detected_error"]["exception_type"] == "RuntimeError"
    serial_tools.esp_serial_monitor_stop(run_id)

    logs = log_tools.esp_logs_get(run_id, tail=100)
    detected_events = [
        event
        for event in logs["events"]
        if event["message"] == "MicroPython runtime error detected."
    ]
    assert len(detected_events) == 1

    parsed = error_tools.esp_error_parse_log(run_id)
    assert parsed["has_error"] is True
    assert parsed["exception_type"] == "RuntimeError"
    assert any(source["kind"] == "sqlite_errors" for source in parsed["scan_sources"])
    assert not any(
        source["kind"] == "serial_monitor_raw"
        for source in parsed["scan_sources"]
    )
    SERIAL_MONITOR_MANAGER.shutdown_all(1)


def test_error_parse_log_reads_only_project_scoped_raw_path():
    scope = log_tools.LogScope.active()
    raw_dir = scope.log_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "traceback.log"
    raw_path.write_text(
        'Traceback (most recent call last):\n  File "worker.py", line 4\nOSError: disconnected',
        encoding="utf-8",
    )
    run_id = log_tools.new_run_id("capture")
    log_tools.start_run("serial_capture", run_id=run_id, scope=scope)
    log_tools.write_event(
        "esp_serial_capture",
        "error",
        "Capture completed with an error.",
        {"raw_path": str(raw_path)},
        run_id=run_id,
        phase="complete",
        scope=scope,
    )
    log_tools.finish_run(run_id, "failed", scope=scope)

    parsed = error_tools.esp_error_parse_log(run_id, max_bytes=4096)

    assert parsed["has_error"] is True
    assert parsed["exception_type"] == "OSError"
    assert parsed["scanned_bytes"] <= 4096
    assert any(source["kind"] == "serial_capture_raw" for source in parsed["scan_sources"])





def test_detector_does_not_treat_normal_log_labels_as_exceptions():
    detector = MicroPythonErrorDetector()

    report = detector.feed("INFO: boot complete\nstate: running\n")

    assert report is None
    parsed = error_tools.esp_error_parse_text("INFO: boot complete\n")
    assert parsed["has_error"] is False


def test_exec_code_automatically_reports_raw_repl_traceback(monkeypatch):
    monkeypatch.setattr(
        exec_tools,
        "execute_code",
        lambda *_args, **_kwargs: {
            "ok": False,
            "stdout": "",
            "stderr": (
                'Traceback (most recent call last):\n'
                '  File "main.py", line 8, in <module>\n'
                "ZeroDivisionError: division by zero"
            ),
            "message": "execution failed",
        },
    )

    result = exec_tools.esp_exec_code(port="COM_TEST", code="1 / 0")

    assert result["has_error"] is True
    assert result["error_report"]["exception_type"] == "ZeroDivisionError"
    assert result["error_report"]["file"] == "main.py"


def test_error_parse_log_uses_persisted_structured_exec_report():
    scope = log_tools.LogScope.active()
    run_id = log_tools.new_run_id("exec")
    report = {
        "has_error": True,
        "error_kind": "micropython_traceback",
        "file": "task.py",
        "line": 17,
        "exception_type": "IndexError",
        "message": "list index out of range",
        "recoverable": True,
        "suggested_next_actions": [],
    }
    log_tools.start_run("exec_code", run_id=run_id, scope=scope)
    log_tools.write_event(
        "esp_exec_code",
        "error",
        "esp_exec_code failed.",
        {"error_report": report, "has_error": True},
        run_id=run_id,
        phase="complete",
        scope=scope,
    )
    log_tools.finish_run(run_id, "failed", scope=scope)

    parsed = error_tools.esp_error_parse_log(run_id)

    assert parsed["has_error"] is True
    assert parsed["exception_type"] == "IndexError"
    assert parsed["file"] == "task.py"
    assert any(
        source["kind"] == "structured_error_report"
        for source in parsed["scan_sources"]
    )

def test_keyboard_interrupt_without_prompt_does_not_confirm_stop(monkeypatch):
    class KeyboardOnlySerial(InterruptSerial):
        def write(self, data: bytes) -> int:
            self.writes.append(data)
            if self.writes.count(b"\x03") == 2:
                self._reads.append(b"KeyboardInterrupt\r\napplication continued")
            return len(data)

    class KeyboardOnlySerialModule:
        Serial = KeyboardOnlySerial

    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: KeyboardOnlySerialModule)

    result = raw_repl_backend.interrupt_program("COM_TEST", timeout_ms=100)

    assert result["ok"] is False
    assert result["observed_keyboard_interrupt"] is True
    assert result["observed_prompt"] is False
    assert result["stop_confirmed"] is False


def test_detector_accepts_custom_exception_name_only_inside_traceback():
    detector = MicroPythonErrorDetector()

    report = detector.feed(
        'Traceback (most recent call last):\n'
        '  File "buzzer.py", line 9, in play\n'
        'BuzzerFault: overcurrent protection\n'
    )

    assert report is not None
    assert report["exception_type"] == "BuzzerFault"
    assert report["file"] == "buzzer.py"
    assert error_tools.esp_error_parse_text("BuzzerFault: idle label")["has_error"] is False


def test_run_file_mpremote_automatically_reports_traceback(monkeypatch):
    monkeypatch.setattr(
        exec_tools.mpremote_backend,
        "run_remote_file",
        lambda **_kwargs: {
            "ok": False,
            "stdout": "",
            "stderr": (
                'Traceback (most recent call last):\n'
                '  File "remote.py", line 3, in <module>\n'
                "BuzzerFault: unsafe duty"
            ),
            "message": "remote failed",
        },
    )

    result = exec_tools.esp_run_file(port="COM_TEST", path="remote.py")

    assert result["has_error"] is True
    assert result["error_report"]["exception_type"] == "BuzzerFault"
    assert result["error_report"]["file"] == "remote.py"


def test_error_parse_log_accepts_structured_monitor_event_without_raw():
    scope = log_tools.LogScope.active()
    run_id = log_tools.new_run_id("structured_monitor_only")
    report = {
        "has_error": True,
        "error_kind": "micropython_traceback",
        "file": "monitor.py",
        "line": 6,
        "exception_type": "MonitorFault",
        "message": "bad sample",
        "recoverable": True,
        "suggested_next_actions": [],
    }
    log_tools.start_run("serial_monitor", run_id=run_id, scope=scope)
    log_tools.write_event(
        "esp_serial_monitor",
        "error",
        "MicroPython runtime error detected.",
        {"has_error": True, "error_report": report},
        run_id=run_id,
        phase="complete",
        scope=scope,
    )
    log_tools.finish_run(run_id, "failed", scope=scope)

    parsed = error_tools.esp_error_parse_log(run_id)

    assert parsed["has_error"] is True
    assert parsed["exception_type"] == "MonitorFault"
    assert any(source["kind"] == "structured_error_report" for source in parsed["scan_sources"])


def test_error_parse_log_rejects_raw_path_outside_project_log_root(tmp_path):
    scope = log_tools.LogScope.active()
    run_id = log_tools.new_run_id("outside_path")
    external = tmp_path / "outside.log"
    external.write_text("ValueError: should not be read", encoding="utf-8")
    log_tools.start_run("serial_capture", run_id=run_id, scope=scope)
    log_tools.write_event(
        "esp_serial_capture",
        "info",
        "capture",
        {"raw_path": str(external)},
        run_id=run_id,
        phase="complete",
        scope=scope,
    )
    log_tools.finish_run(run_id, "succeeded", scope=scope)

    parsed = error_tools.esp_error_parse_log(run_id)

    assert parsed["has_error"] is False
    assert {
        item["reason"] for item in parsed["skipped_sources"]
    } == {"legacy raw path is outside the active project log root"}
    assert not any(source["kind"] == "serial_capture_raw" for source in parsed["scan_sources"])


def test_error_parse_log_honors_scan_limit_before_late_traceback():
    scope = log_tools.LogScope.active()
    raw_dir = scope.log_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "bounded-late-traceback.log"
    raw_path.write_text(
        "x" * 5000
        + '\nTraceback (most recent call last):\n  File "late.py", line 1\nLateFault: late',
        encoding="utf-8",
    )
    run_id = log_tools.new_run_id("bounded")
    log_tools.start_run("serial_capture", run_id=run_id, scope=scope)
    log_tools.write_event(
        "esp_serial_capture",
        "info",
        "capture",
        {"raw_path": str(raw_path)},
        run_id=run_id,
        phase="complete",
        scope=scope,
    )
    log_tools.finish_run(run_id, "succeeded", scope=scope)

    parsed = error_tools.esp_error_parse_log(run_id, max_bytes=4096)

    assert parsed["scanned_bytes"] == 4096
    assert parsed["scan_truncated"] is True
    assert parsed["has_error"] is False
