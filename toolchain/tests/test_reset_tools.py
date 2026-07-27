from __future__ import annotations

import base64
import hashlib

from esp_mcp_toolchain.tools import log_tools, reset_tools


class FakeSerial:
    writes: list[bytes] = []
    instances: list["FakeSerial"] = []
    events: list[tuple[str, object]] = []
    fail_on: str | None = None

    def __init__(self):
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.write_timeout = None
        self.rtscts = True
        self.dsrdtr = True
        self.xonxoff = True
        self._dtr = True
        self._rts = True
        self._hard_pulse_asserted = False
        self._release_failed = False
        self._post_open_rts_failed = False
        self._action_started = False
        self._write_count = 0
        self._read_failed = False
        self.is_open = False
        self.closed = False
        self.open_snapshot: dict[str, object] = {}
        self._pre_action_reads: list[bytes] = []
        self._reads = (
            [b"\xffboot\r\n"]
            if type(self).fail_on == "invalid_utf8"
            else [b"soft reboot\r\n", b"MicroPython\r\n>>> "]
        )
        type(self).instances.append(self)
        type(self).events.append(("construct", None))

    @classmethod
    def reset(cls, *, fail_on: str | None = None) -> None:
        cls.writes = []
        cls.instances = []
        cls.events = []
        cls.fail_on = fail_on

    @property
    def dtr(self) -> bool:
        return self._dtr

    @dtr.setter
    def dtr(self, value: bool) -> None:
        type(self).events.append(("dtr", value))
        if type(self).fail_on == "dtr_setup" and not self.is_open:
            raise OSError("DTR setup failed")
        self._dtr = value

    @property
    def rts(self) -> bool:
        return self._rts

    @rts.setter
    def rts(self, value: bool) -> None:
        type(self).events.append(("rts", value))
        if (
            value is False
            and self.is_open
            and type(self).fail_on == "post_open_rts"
            and not self._post_open_rts_failed
        ):
            self._post_open_rts_failed = True
            raise OSError("post-open RTS setup failed")
        if value is True:
            if type(self).fail_on == "rts_assert":
                raise OSError("RTS assert failed")
            self._hard_pulse_asserted = True
            self._action_started = True
        if (
            value is False
            and self._hard_pulse_asserted
            and type(self).fail_on in {"rts_release", "rts_release_persistent"}
            and (
                type(self).fail_on == "rts_release_persistent"
                or not self._release_failed
            )
        ):
            self._release_failed = True
            raise OSError("RTS release failed")
        self._rts = value

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
        type(self).events.append(("open", dict(self.open_snapshot)))
        if type(self).fail_on == "open":
            raise OSError(5, "Access is denied")
        if type(self).fail_on == "driver_line_change":
            self._dtr = True
            self._rts = True
            self._pre_action_reads = [b"rst:0x1 (POWERON_RESET)\r\n"]
        self.is_open = True

    def close(self) -> None:
        type(self).events.append(("close", None))
        if type(self).fail_on == "close":
            raise OSError("close failed")
        self.closed = True
        self.is_open = False

    def write(self, data: bytes) -> int:
        self._action_started = True
        self._write_count += 1
        type(self).events.append(("write", data))
        if type(self).fail_on == f"write_{self._write_count}":
            raise OSError(f"write {self._write_count} failed")
        if type(self).fail_on == f"partial_write_{self._write_count}":
            return 0
        self.writes.append(data)
        return len(data)

    def reset_input_buffer(self) -> None:
        type(self).events.append(("reset_input_buffer", None))

    def flush(self) -> None:
        type(self).events.append(("flush", None))

    def read(self, _size: int) -> bytes:
        type(self).events.append(("read", None))
        if not self._action_started:
            if type(self).fail_on == "pre_action_read" and not self._read_failed:
                self._read_failed = True
                raise OSError("pre-action read failed")
            if self._pre_action_reads:
                return self._pre_action_reads.pop(0)
            return b""
        if type(self).fail_on == "read" and not self._read_failed:
            self._read_failed = True
            raise OSError("read failed")
        if self._reads:
            return self._reads.pop(0)
        return b""


class FakeSerialModule:
    Serial = FakeSerial


def prepare_reset(monkeypatch, *, fail_on: str | None = None) -> None:
    FakeSerial.reset(fail_on=fail_on)
    monkeypatch.setattr(reset_tools, "get_serial_module", lambda: FakeSerialModule)
    monkeypatch.setattr(reset_tools, "_sleep", lambda _seconds: None)
    clock = [0.0]

    def monotonic() -> float:
        value = clock[0]
        clock[0] += 0.1
        return value

    monkeypatch.setattr(reset_tools, "_monotonic", monotonic)


def test_reset_soft_sends_ctrl_c_ctrl_d(monkeypatch):
    prepare_reset(monkeypatch)

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")
    expected_output = b"soft reboot\r\nMicroPython\r\n>>> "

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_reset"
    assert FakeSerial.writes == [b"\x03", b"\x04"]
    assert "MicroPython" in result["text"]
    assert result["reset_command_sent"] is True
    assert result["hard_reset_pulse_started"] is False
    assert result["hard_reset_pulse_completed"] is False
    assert result["hard_reset_line_released"] is False
    assert result["reset_confirmed"] is False
    assert result["physical_reset_excluded"] is False
    assert result["pre_action_bytes_read"] == 0
    assert result["pre_action_output_observed"] is False
    assert result["pre_action_text"] == ""
    assert result["reset_output_bytes"] == len(expected_output)
    assert result["reset_output_text"] == expected_output.decode("utf-8")
    assert base64.b64decode(result["reset_output_raw_base64"]) == expected_output
    assert result["reset_output_sha256"] == hashlib.sha256(expected_output).hexdigest()
    assert result["reset_output_decode_error"] is False
    assert result["reset_output_capture_completed"] is True
    assert result["reset_output_capture_limit_reached"] is False
    assert result["output_causality_confirmed"] is False
    assert result["cleanup_completed"] is True
    assert result["cleanup_required"] is True
    assert result["cleanup_attempted"] is True
    assert result["failure_stage"] is None
    assert "did not independently confirm" in result["message"]
    assert all(value is False for name, value in FakeSerial.events if name in {"dtr", "rts"})
    assert FakeSerial.instances[0].closed is True

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    assert complete["payload_json"]["reset_command_sent"] is True
    assert complete["payload_json"]["failure_stage"] is None
    assert complete["payload_json"]["cleanup_errors"] == []
    assert complete["payload_json"]["reset_output_bytes"] == len(expected_output)
    assert complete["payload_json"]["reset_output_text"] == expected_output.decode("utf-8")
    assert base64.b64decode(complete["payload_json"]["reset_output_raw_base64"]) == expected_output
    assert complete["payload_json"]["reset_output_sha256"] == hashlib.sha256(expected_output).hexdigest()
    assert complete["payload_json"]["reset_output_decode_error"] is False
    assert complete["payload_json"]["reset_output_capture_completed"] is True
    assert complete["payload_json"]["reset_output_capture_limit_reached"] is False


def test_reset_hard_restarts_app_without_asserting_boot_pin(monkeypatch):
    prepare_reset(monkeypatch)

    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_reset"
    assert result["mode"] == "hard"
    assert result["reset_command_sent"] is False
    assert result["hard_reset_pulse_started"] is True
    assert result["hard_reset_pulse_completed"] is True
    assert result["hard_reset_line_released"] is True
    assert result["reset_confirmed"] is False
    assert result["physical_reset_excluded"] is False
    assert [value for name, value in FakeSerial.events if name == "dtr"] == [False, False, False, False]
    assert [value for name, value in FakeSerial.events if name == "rts"] == [
        False,
        False,
        True,
        False,
        False,
    ]
    assert "MicroPython" in result["text"]
    assert FakeSerial.instances[0].closed is True


def test_reset_persists_exact_binary_output_and_decode_status(monkeypatch):
    prepare_reset(monkeypatch, fail_on="invalid_utf8")
    expected_output = b"\xffboot\r\n"

    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is True
    assert result["reset_output_bytes"] == len(expected_output)
    assert base64.b64decode(result["reset_output_raw_base64"]) == expected_output
    assert result["reset_output_sha256"] == hashlib.sha256(expected_output).hexdigest()
    assert result["reset_output_decode_error"] is True
    assert "\ufffdboot" in result["reset_output_text"]

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    assert base64.b64decode(complete["payload_json"]["reset_output_raw_base64"]) == expected_output
    assert complete["payload_json"]["reset_output_decode_error"] is True


def test_reset_persists_capture_limit_status(monkeypatch):
    prepare_reset(monkeypatch)
    calls = 0

    def bounded_read(_ser, _duration_s: float, *, max_bytes: int) -> tuple[bytes, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return b"", False
        assert max_bytes == 65_536
        return b"bounded reset output", True

    monkeypatch.setattr(reset_tools, "_read_for", bounded_read)

    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is True
    assert result["output_capture_limit_reached"] is True
    assert result["reset_output_capture_limit_reached"] is True
    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    assert complete["payload_json"]["reset_output_capture_limit_reached"] is True


def test_reset_configures_safe_control_lines_before_open(monkeypatch):
    prepare_reset(monkeypatch, fail_on="driver_line_change")

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is True
    assert FakeSerial.instances[0].open_snapshot == {
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
    assert FakeSerial.instances[0].dtr is False
    assert FakeSerial.instances[0].rts is False
    assert result["pre_action_output_observed"] is True
    assert result["pre_action_bytes_read"] == len(b"rst:0x1 (POWERON_RESET)\r\n")
    assert "POWERON_RESET" in result["pre_action_text"]
    assert "POWERON_RESET" not in result["text"]
    assert "inspect pre_action_text" in result["message"]
    assert not any(name == "reset_input_buffer" for name, _value in FakeSerial.events)
    assert not any(name == "flush" for name, _value in FakeSerial.events)

    open_index = next(index for index, event in enumerate(FakeSerial.events) if event[0] == "open")
    first_read_index = next(index for index, event in enumerate(FakeSerial.events) if event[0] == "read")
    first_write_index = next(index for index, event in enumerate(FakeSerial.events) if event[0] == "write")
    post_open_dtr_index = next(
        index
        for index, event in enumerate(FakeSerial.events)
        if index > open_index and event == ("dtr", False)
    )
    post_open_rts_index = next(
        index
        for index, event in enumerate(FakeSerial.events)
        if index > open_index and event == ("rts", False)
    )
    assert open_index < post_open_dtr_index < first_read_index < first_write_index
    assert open_index < post_open_rts_index < first_read_index < first_write_index

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    assert "POWERON_RESET" in complete["payload_json"]["pre_action_text"]


def test_reset_closes_partial_handle_when_open_fails(monkeypatch):
    prepare_reset(monkeypatch, fail_on="open")

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is False
    assert result["error_kind"] == "reset_failed"
    assert result["failure_stage"] == "open"
    assert result["physical_reset_excluded"] is False
    assert result["reset_command_sent"] is False
    assert result["cleanup_completed"] is True
    assert result["cleanup_attempted"] is True
    assert FakeSerial.instances[0].closed is True


def test_reset_stops_before_open_when_control_line_setup_fails(monkeypatch):
    prepare_reset(monkeypatch, fail_on="dtr_setup")

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is False
    assert result["failure_stage"] == "setup"
    assert result["physical_reset_excluded"] is True
    assert not any(name == "open" for name, _value in FakeSerial.events)
    assert any(name == "close" for name, _value in FakeSerial.events)


def test_reset_reports_post_open_control_line_failure_precisely(monkeypatch):
    prepare_reset(monkeypatch, fail_on="post_open_rts")

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is False
    assert result["failure_stage"] == "post_open_control_lines"
    assert result["serial_opened"] is True
    assert result["reset_command_sent"] is False
    assert result["cleanup_completed"] is True
    assert FakeSerial.instances[0].rts is False
    assert FakeSerial.instances[0].closed is True


def test_reset_reports_pre_action_capture_failure_without_sending_reset(monkeypatch):
    prepare_reset(monkeypatch, fail_on="pre_action_read")

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is False
    assert result["failure_stage"] == "pre_action_capture"
    assert result["serial_opened"] is True
    assert result["reset_command_sent"] is False
    assert result["cleanup_completed"] is True
    assert FakeSerial.instances[0].closed is True


def test_reset_reports_ctrl_d_partial_write_without_claiming_command_sent(monkeypatch):
    prepare_reset(monkeypatch, fail_on="partial_write_2")

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is False
    assert result["error_kind"] == "reset_failed"
    assert result["failure_stage"] == "soft_reset_command"
    assert result["reset_command_sent"] is False
    assert result["physical_reset_excluded"] is False
    assert FakeSerial.instances[0].closed is True


def test_reset_preserves_action_state_when_capture_fails(monkeypatch):
    prepare_reset(monkeypatch, fail_on="read")

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is False
    assert result["failure_stage"] == "capture"
    assert result["reset_command_sent"] is True
    assert result["reset_confirmed"] is False
    assert result["reset_output_bytes"] == 0
    assert result["reset_output_sha256"] is None
    assert result["reset_output_raw_base64"] == ""
    assert result["reset_output_text"] == ""
    assert result["reset_output_decode_error"] is False
    assert result["reset_output_capture_completed"] is False
    assert result["reset_output_capture_limit_reached"] is False
    assert FakeSerial.instances[0].closed is True


def test_reset_retries_rts_cleanup_after_hard_release_fails(monkeypatch):
    prepare_reset(monkeypatch, fail_on="rts_release")

    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is False
    assert result["failure_stage"] == "hard_release"
    assert result["reset_command_sent"] is False
    assert result["hard_reset_pulse_started"] is True
    assert result["hard_reset_pulse_completed"] is False
    assert result["hard_reset_line_released"] is True
    assert result["cleanup_completed"] is True
    assert FakeSerial.instances[0].rts is False
    assert FakeSerial.instances[0].closed is True


def test_reset_reports_persistent_rts_release_failure(monkeypatch):
    prepare_reset(monkeypatch, fail_on="rts_release_persistent")

    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is False
    assert result["failure_stage"] == "hard_release"
    assert result["hard_reset_pulse_started"] is True
    assert result["hard_reset_pulse_completed"] is False
    assert result["hard_reset_line_released"] is False
    assert result["cleanup_completed"] is False
    assert any(item.startswith("rts_cleanup:") for item in result["cleanup_errors"])
    assert FakeSerial.instances[0].closed is True


def test_reset_releases_rts_when_hard_pulse_delay_fails(monkeypatch):
    prepare_reset(monkeypatch)

    def fail_delay(_seconds: float) -> None:
        raise OSError("delay failed")

    monkeypatch.setattr(reset_tools, "_sleep", fail_delay)

    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is False
    assert result["failure_stage"] == "hard_delay"
    assert result["hard_reset_pulse_started"] is True
    assert result["hard_reset_pulse_completed"] is False
    assert result["hard_reset_line_released"] is True
    assert FakeSerial.instances[0].rts is False
    assert FakeSerial.instances[0].closed is True


def test_reset_does_not_claim_hard_pulse_when_rts_assert_fails(monkeypatch):
    prepare_reset(monkeypatch, fail_on="rts_assert")

    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is False
    assert result["failure_stage"] == "hard_assert"
    assert result["hard_reset_pulse_started"] is False
    assert result["hard_reset_pulse_completed"] is False
    assert result["hard_reset_line_released"] is False
    assert result["reset_command_sent"] is False
    assert FakeSerial.instances[0].closed is True


def test_reset_does_not_hide_close_failure(monkeypatch):
    prepare_reset(monkeypatch, fail_on="close")

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is False
    assert result["error_kind"] == "reset_cleanup_failed"
    assert result["failure_stage"] == "cleanup"
    assert result["reset_command_sent"] is True
    assert result["cleanup_completed"] is False
    assert any(item.startswith("close:") for item in result["cleanup_errors"])


def test_reset_reports_missing_pyserial_without_claiming_physical_access(monkeypatch):
    monkeypatch.setattr(reset_tools, "get_serial_module", lambda: None)

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is False
    assert result["error_kind"] == "pyserial_missing"
    assert result["physical_reset_excluded"] is True
    assert result["serial_opened"] is False
    assert result["cleanup_required"] is False
    assert result["cleanup_attempted"] is False


def test_reset_reports_missing_port_without_constructing_serial(monkeypatch):
    FakeSerial.reset()
    monkeypatch.setattr(reset_tools, "get_serial_module", lambda: FakeSerialModule)
    monkeypatch.setattr(reset_tools, "get_selected_port", lambda: None)

    result = reset_tools.esp_reset(port=None, mode="soft")

    assert result["ok"] is False
    assert result["error_kind"] == "serial_port_not_selected"
    assert result["physical_reset_excluded"] is True
    assert FakeSerial.instances == []
    assert result["cleanup_required"] is False
    assert result["cleanup_attempted"] is False


def test_reset_rejects_unknown_mode():
    result = reset_tools.esp_reset(port="COM_TEST", mode="unknown")

    assert result["ok"] is False
    assert result["error_kind"] == "unsupported_reset_mode"
    assert result["implemented"] is True
    assert result["physical_reset_excluded"] is True
    assert result["reset_command_sent"] is False
    assert result["cleanup_required"] is False
    assert result["cleanup_attempted"] is False
