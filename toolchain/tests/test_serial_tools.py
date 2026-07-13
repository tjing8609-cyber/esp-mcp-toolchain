from __future__ import annotations

import base64
import json
from pathlib import Path
from queue import Empty, Queue
import threading
import time

import pytest

from esp_mcp_toolchain.backends.serial_monitor_backend import SERIAL_MONITOR_MANAGER, SerialMonitorManager
from esp_mcp_toolchain.backends.serial_monitor_lock import identity_key
from esp_mcp_toolchain.backends.serial_monitor_store import SerialLogStore
from esp_mcp_toolchain.project_context import get_project_context, select_project_context
from esp_mcp_toolchain.tools import serial_tools


class FakeSerial:
    queue: Queue = Queue()
    instances: list["FakeSerial"] = []
    open_gate: threading.Event | None = None
    open_error: BaseException | None = None

    def __init__(self):
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.rtscts = True
        self.dsrdtr = True
        self.xonxoff = True
        self.dtr = True
        self.rts = True
        self.is_open = False
        self.closed = threading.Event()
        type(self).instances.append(self)

    def open(self):
        if type(self).open_gate is not None:
            type(self).open_gate.wait(3)
        if type(self).open_error is not None:
            raise type(self).open_error
        self.is_open = True

    def read(self, _size: int):
        if self.closed.is_set():
            return b""
        try:
            item = type(self).queue.get(timeout=0.02)
        except Empty:
            return b""
        if isinstance(item, BaseException):
            raise item
        return item

    def cancel_read(self):
        type(self).queue.put(b"")

    def close(self):
        self.closed.set()
        self.is_open = False


class FakeSerialModule:
    Serial = FakeSerial


def _identity(port: str) -> dict:
    return {
        "port": port,
        "device_path": port,
        "vid": "FFFF",
        "pid": "0001",
        "serial_number": port,
        "location": "test-location",
    }


def _wait_for_state(run_id: str, expected: set[str], timeout: float = 3) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = serial_tools.esp_serial_monitor_status(run_id)
        if result["monitors"] and result["monitors"][0]["state"] in expected:
            return result["monitors"][0]
        time.sleep(0.01)
    raise AssertionError(f"Monitor {run_id} did not reach {expected}")


@pytest.fixture(autouse=True)
def fake_monitor(monkeypatch):
    SERIAL_MONITOR_MANAGER.shutdown_all(1)
    FakeSerial.queue = Queue()
    FakeSerial.instances = []
    FakeSerial.open_gate = None
    FakeSerial.open_error = None
    monkeypatch.setattr(serial_tools, "get_serial_module", lambda: FakeSerialModule)
    monkeypatch.setattr(serial_tools, "describe_serial_port", _identity)
    yield
    SERIAL_MONITOR_MANAGER.shutdown_all(1)


def test_serial_monitor_status_empty():
    result = serial_tools.esp_serial_monitor_status()
    assert result == {"ok": True, "monitors": []}


def test_monitor_start_read_cursor_binary_and_stop():
    start = serial_tools.esp_serial_monitor_start("COM_TEST", session_name="capture")
    assert start["ok"] is True
    assert start["state"] == "RUNNING"
    run_id = start["run_id"]
    assert start["monitor"]["project_id"]
    assert start["monitor"]["port_identity"]["serial_number"] == "COM_TEST"
    assert FakeSerial.instances[0].dtr is False
    assert FakeSerial.instances[0].rts is False

    FakeSerial.queue.put(b"A\xe4\xb8")
    FakeSerial.queue.put(b"\xadB\xff")
    first = serial_tools.esp_serial_monitor_read(run_id, representation="both", wait_ms=1000)

    assert first["ok"] is True
    assert "".join(record["text"] for record in first["records"]) == "A\u4e2dB\ufffd"
    assert [record["seq"] for record in first["records"]] == [1, 2]
    assert any(record["decode_error"] for record in first["records"])
    assert first["next_after_seq"] == 2
    decoded_raw = b"".join(base64.b64decode(record["raw_base64"]) for record in first["records"])
    assert decoded_raw == b"A\xe4\xb8\xadB\xff"

    no_repeat = serial_tools.esp_serial_monitor_read(run_id, after_seq=first["next_after_seq"])
    assert no_repeat["records"] == []
    assert no_repeat["next_after_seq"] == 2

    stopped = serial_tools.esp_serial_monitor_stop(run_id)
    assert stopped["ok"] is True
    assert stopped["monitor"]["state"] == "STOPPED"
    assert stopped["monitor"]["worker_alive"] is False
    assert serial_tools.esp_serial_monitor_stop(run_id)["ok"] is True

    context = get_project_context()
    fresh_manager = SerialMonitorManager()
    persisted = fresh_manager.read(
        project_id=context["project_id"],
        log_root=Path(context["project_dir"]) / "logs",
        run_id=run_id,
        after_seq=None,
        max_bytes=65_536,
        wait_ms=0,
        representation="both",
    )
    persisted_raw = b"".join(base64.b64decode(record["raw_base64"]) for record in persisted["records"])
    assert persisted_raw == decoded_raw
    assert persisted["state"] == "STOPPED"


def test_monitor_read_never_exceeds_max_bytes_even_if_backend_returns_a_large_chunk():
    start = serial_tools.esp_serial_monitor_start("COM_LARGE")
    run_id = start["run_id"]
    payload = b"x" * 9000
    FakeSerial.queue.put(payload)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = serial_tools.esp_serial_monitor_status(run_id)["monitors"][0]
        if status["bytes_received"] == len(payload):
            break
        time.sleep(0.01)

    reconstructed = bytearray()
    cursor = None
    while len(reconstructed) < len(payload):
        result = serial_tools.esp_serial_monitor_read(
            run_id,
            after_seq=cursor,
            max_bytes=4096,
            representation="base64",
        )
        response_bytes = sum(record["raw_size"] for record in result["records"])
        assert response_bytes <= 4096
        assert result["records"]
        for record in result["records"]:
            reconstructed.extend(base64.b64decode(record["raw_base64"]))
        cursor = result["next_after_seq"]

    assert bytes(reconstructed) == payload
    serial_tools.esp_serial_monitor_stop(run_id)


def test_monitor_ring_buffer_reports_dropped_cursor(monkeypatch):
    monkeypatch.setenv("ESP_MCP_MONITOR_BUFFER_BYTES", "4")
    start = serial_tools.esp_serial_monitor_start("COM_DROP")
    run_id = start["run_id"]
    FakeSerial.queue.put(b"1234")
    FakeSerial.queue.put(b"5678")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = serial_tools.esp_serial_monitor_status(run_id)["monitors"][0]
        if status["bytes_received"] == 8:
            break
        time.sleep(0.01)

    result = serial_tools.esp_serial_monitor_read(run_id, after_seq=0)
    assert [record["text"] for record in result["records"]] == ["5678"]
    assert result["dropped_before_seq"] == 1
    assert serial_tools.esp_serial_monitor_status(run_id)["monitors"][0]["dropped_bytes"] == 4


def test_monitor_rotates_raw_chunks_and_records_checksums(monkeypatch):
    monkeypatch.setenv("ESP_MCP_MONITOR_CHUNK_BYTES", "4")
    start = serial_tools.esp_serial_monitor_start("COM_ROTATE")
    run_id = start["run_id"]
    FakeSerial.queue.put(b"1234")
    FakeSerial.queue.put(b"5678")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = serial_tools.esp_serial_monitor_status(run_id)["monitors"][0]
        if status["bytes_received"] == 8:
            break
        time.sleep(0.01)
    serial_tools.esp_serial_monitor_stop(run_id)

    manifest_path = Path(status["log_dir"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [chunk["byte_length"] for chunk in manifest["chunks"]] == [4, 4]
    assert all(len(chunk["sha256"]) == 64 for chunk in manifest["chunks"])
    assert not list(Path(status["log_dir"]).glob("*.part"))


def test_monitor_project_raw_quota_stops_before_overflow(monkeypatch):
    monkeypatch.setenv("ESP_MCP_MONITOR_PROJECT_BYTES", "6")
    start = serial_tools.esp_serial_monitor_start("COM_PROJECT_QUOTA")
    run_id = start["run_id"]
    FakeSerial.queue.put(b"1234")
    FakeSerial.queue.put(b"5678")

    status = _wait_for_state(run_id, {"FAILED"})
    assert status["last_error"]["error_kind"] == "serial_log_quota_exceeded"
    assert status["persisted_bytes"] == 4
    assert status["unpersisted_bytes"] == 4


def test_monitor_is_bound_to_starting_project(tmp_path, monkeypatch):
    monkeypatch.setenv("ESP_MCP_DATA_ROOT", str(tmp_path / "data"))
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_context = select_project_context(first)
    start = serial_tools.esp_serial_monitor_start("COM_BOUND")
    run_id = start["run_id"]

    second_context = select_project_context(second)
    FakeSerial.queue.put(b"still-first")
    assert serial_tools.esp_serial_monitor_status(run_id)["monitors"] == []
    assert serial_tools.esp_serial_monitor_stop(run_id)["error_kind"] == "monitor_run_not_found"

    select_project_context(first)
    read = serial_tools.esp_serial_monitor_read(run_id, wait_ms=1000)
    assert "".join(record["text"] for record in read["records"]) == "still-first"
    status = serial_tools.esp_serial_monitor_status(run_id)["monitors"][0]
    assert status["project_id"] == first_context["project_id"]
    assert second_context["project_id"] != status["project_id"]
    assert str(tmp_path / "data" / first_context["project_id"]) in status["log_dir"]
    serial_tools.esp_serial_monitor_stop(run_id)
    first_events = tmp_path / "data" / first_context["project_id"] / "logs" / "sessions" / f"{run_id}.jsonl"
    second_events = tmp_path / "data" / second_context["project_id"] / "logs" / "sessions" / f"{run_id}.jsonl"
    assert first_events.exists()
    assert not second_events.exists()


def test_monitor_disconnect_preserves_buffer_and_terminal_reason():
    start = serial_tools.esp_serial_monitor_start("COM_GONE")
    run_id = start["run_id"]
    FakeSerial.queue.put(b"before-disconnect")
    FakeSerial.queue.put(RuntimeError("device disconnected"))

    status = _wait_for_state(run_id, {"DISCONNECTED"})
    assert status["last_error"]["error_kind"] == "serial_disconnected"
    deadline = time.monotonic() + 1
    while status["worker_alive"] and time.monotonic() < deadline:
        time.sleep(0.01)
        status = serial_tools.esp_serial_monitor_status(run_id)["monitors"][0]
    assert status["worker_alive"] is False
    result = serial_tools.esp_serial_monitor_read(run_id)
    assert "".join(record["text"] for record in result["records"]) == "before-disconnect"
    assert result["state"] == "DISCONNECTED"
    assert serial_tools.esp_serial_monitor_stop(run_id)["ok"] is True


def test_monitor_quota_failure_is_not_silent(monkeypatch):
    monkeypatch.setenv("ESP_MCP_MONITOR_SESSION_BYTES", "4")
    start = serial_tools.esp_serial_monitor_start("COM_QUOTA")
    run_id = start["run_id"]
    FakeSerial.queue.put(b"12345")

    status = _wait_for_state(run_id, {"FAILED"})
    assert status["last_error"]["error_kind"] == "serial_log_quota_exceeded"
    assert status["unpersisted_bytes"] == 5


def test_monitor_disk_full_failure_is_not_silent(monkeypatch):
    def fail_append(self, seq, timestamp_utc, raw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(SerialLogStore, "append", fail_append)
    start = serial_tools.esp_serial_monitor_start("COM_FULL")
    run_id = start["run_id"]
    FakeSerial.queue.put(b"not-persisted")

    status = _wait_for_state(run_id, {"FAILED"})
    assert status["last_error"]["error_kind"] == "serial_log_disk_full"
    assert status["unpersisted_bytes"] == len(b"not-persisted")


def test_monitor_high_frequency_output_is_bounded_and_accounted():
    start = serial_tools.esp_serial_monitor_start("COM_FAST")
    run_id = start["run_id"]
    payloads = [bytes([index % 251]) * 4096 for index in range(64)]
    for payload in payloads:
        FakeSerial.queue.put(payload)
    expected = sum(map(len, payloads))
    deadline = time.monotonic() + 4
    status = {}
    while time.monotonic() < deadline:
        status = serial_tools.esp_serial_monitor_status(run_id)["monitors"][0]
        if status["bytes_received"] == expected:
            break
        time.sleep(0.01)

    assert status["bytes_received"] == expected
    assert status["buffered_bytes"] <= 1024 * 1024
    assert status["persisted_bytes"] == expected


def test_monitor_concurrent_start_and_stop():
    barrier = threading.Barrier(3)
    starts: list[dict] = []

    def start_monitor():
        barrier.wait()
        starts.append(serial_tools.esp_serial_monitor_start("COM_RACE", session_name="race"))

    threads = [threading.Thread(target=start_monitor) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sum(result["ok"] for result in starts) == 1
    failed = next(result for result in starts if not result["ok"])
    assert failed["error_kind"] in {"monitor_session_conflict", "serial_port_monitored"}
    successful = next(result for result in starts if result["ok"])
    run_id = successful["run_id"]
    stops: list[dict] = []
    stop_barrier = threading.Barrier(3)

    def stop_monitor():
        stop_barrier.wait()
        stops.append(serial_tools.esp_serial_monitor_stop(run_id))

    stop_threads = [threading.Thread(target=stop_monitor) for _ in range(2)]
    for thread in stop_threads:
        thread.start()
    stop_barrier.wait()
    for thread in stop_threads:
        thread.join()

    assert all(result["ok"] for result in stops)
    assert all(result["monitor"]["state"] == "STOPPED" for result in stops)


def test_monitor_waiting_read_returns_when_stopped():
    start = serial_tools.esp_serial_monitor_start("COM_WAIT")
    run_id = start["run_id"]
    reads: list[dict] = []

    thread = threading.Thread(
        target=lambda: reads.append(serial_tools.esp_serial_monitor_read(run_id, after_seq=0, wait_ms=3000))
    )
    thread.start()
    time.sleep(0.05)
    serial_tools.esp_serial_monitor_stop(run_id)
    thread.join(1)

    assert not thread.is_alive()
    assert reads[0]["records"] == []
    assert reads[0]["state"] == "STOPPED"


def test_monitor_stop_while_starting_is_bounded():
    gate = threading.Event()
    FakeSerial.open_gate = gate
    starts: list[dict] = []
    start_thread = threading.Thread(
        target=lambda: starts.append(serial_tools.esp_serial_monitor_start("COM_STARTING", session_name="starting"))
    )
    start_thread.start()
    deadline = time.monotonic() + 1
    monitors = []
    while time.monotonic() < deadline:
        monitors = serial_tools.esp_serial_monitor_status()["monitors"]
        if monitors:
            break
        time.sleep(0.01)
    assert monitors[0]["state"] == "STARTING"
    stopped: list[dict] = []
    stop_thread = threading.Thread(
        target=lambda: stopped.append(serial_tools.esp_serial_monitor_stop(monitors[0]["run_id"], timeout_ms=1000))
    )
    stop_thread.start()
    time.sleep(0.05)
    gate.set()
    start_thread.join(2)
    stop_thread.join(2)

    assert not start_thread.is_alive()
    assert not stop_thread.is_alive()
    assert stopped[0]["ok"] is True
    assert stopped[0]["monitor"]["state"] == "STOPPED"


def test_port_identity_survives_com_number_change():
    first = _identity("COM3")
    second = {**first, "port": "COM5", "device_path": "COM5"}
    assert identity_key(first) == identity_key(second)


def test_monitor_reports_port_open_failure_and_closes_partial_handle():
    FakeSerial.open_error = OSError(5, "Access is denied")
    result = serial_tools.esp_serial_monitor_start("COM_BUSY")

    assert result["ok"] is False
    assert result["error_kind"] == "serial_port_open_failed"
    assert FakeSerial.instances[0].closed.is_set()


@pytest.mark.parametrize("session_name", ["../escape", "", "space name", "x" * 65])
def test_monitor_rejects_unsafe_session_names(session_name):
    result = serial_tools.esp_serial_monitor_start("COM_TEST", session_name=session_name)
    assert result["ok"] is False
    assert result["error_kind"] == "invalid_session_name"


def test_monitor_read_validates_cursor_limits_and_representation():
    assert serial_tools.esp_serial_monitor_read("missing", after_seq=-1)["error_kind"] == "invalid_cursor"
    assert serial_tools.esp_serial_monitor_read("missing", max_bytes=1)["error_kind"] == "invalid_max_bytes"
    assert serial_tools.esp_serial_monitor_read("missing", wait_ms=30_001)["error_kind"] == "invalid_wait"
    assert serial_tools.esp_serial_monitor_read("missing", representation="lines")["error_kind"] == "invalid_representation"


def test_monitor_tools_reject_unsafe_run_ids():
    unsafe = "..\\..\\another-project"
    assert serial_tools.esp_serial_monitor_status(unsafe)["error_kind"] == "invalid_monitor_run_id"
    assert serial_tools.esp_serial_monitor_stop(unsafe)["error_kind"] == "invalid_monitor_run_id"
    assert serial_tools.esp_serial_monitor_read(unsafe)["error_kind"] == "invalid_monitor_run_id"
