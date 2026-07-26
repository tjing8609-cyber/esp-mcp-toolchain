from __future__ import annotations

import pytest

from esp_mcp_toolchain.backends import raw_repl_backend
from esp_mcp_toolchain.tools import exec_tools


class FakeSerial:
    instances: list["FakeSerial"] = []
    execution_chunks: list[bytes] = []
    entry_chunks: list[bytes] = []
    fail_on: str | None = None

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
        self.is_open = False
        self.closed = False
        self.open_snapshot: dict[str, object] = {}
        self._reads: list[bytes] = []
        self.writes: list[bytes] = []
        type(self).instances.append(self)

    @classmethod
    def reset(
        cls,
        *,
        execution_chunks: list[bytes] | None = None,
        entry_chunks: list[bytes] | None = None,
        fail_on: str | None = None,
    ) -> None:
        cls.instances = []
        cls.execution_chunks = list(execution_chunks or [])
        cls.entry_chunks = list(
            entry_chunks
            if entry_chunks is not None
            else [b"raw REPL; CTRL-B to exit\r\n>"]
        )
        cls.fail_on = fail_on

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
        self.is_open = True

    def reset_input_buffer(self) -> None:
        self._reads.clear()

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        if data == b"\x02" and type(self).fail_on == "raw_repl_exit":
            raise OSError("raw REPL exit failed")
        if data == b"\x02" and type(self).fail_on == "raw_repl_exit_short_write":
            return 0
        if data.endswith(b"\x04") and type(self).fail_on == "code_short_write":
            return max(0, len(data) - 1)
        if data == b"\x01":
            self._reads.extend(type(self).entry_chunks)
        elif data.endswith(b"\x04"):
            self._reads.extend(type(self).execution_chunks)
        return len(data)

    def read(self, _size: int) -> bytes:
        if self._reads:
            return self._reads.pop(0)
        return b""

    def close(self) -> None:
        if type(self).fail_on == "close":
            raise OSError("close failed")
        self.closed = True


class FakeSerialModule:
    Serial = FakeSerial


def test_raw_repl_execute_code_parses_stdout(monkeypatch):
    FakeSerial.reset(
        execution_chunks=[b"OKhello\r\n\x04", b"\x04>"],
    )
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('hello')",
        timeout_ms=100,
    )

    assert result["ok"] is True
    assert result["stdout"] == "hello\r\n"
    assert result["stderr"] == ""
    assert result["execution_acknowledged"] is True
    assert result["stdout_eot_observed"] is True
    assert result["stderr_eot_observed"] is True
    assert result["raw_repl_prompt_observed"] is True
    assert result["execution_completed"] is True
    assert result["cleanup_completed"] is True
    assert result["serial_cleanup_completed"] is True
    assert result["cleanup_errors"] == []
    assert result["raw_repl_exit_sent"] is True
    assert result["raw_repl_exit_write_count"] == 1
    assert result["raw_repl_exit_confirmed"] is False
    assert result["raw_repl_exit_completed"] is False
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
    assert FakeSerial.instances[0].closed is True


def test_raw_repl_ack_without_eot_is_not_reported_as_success(monkeypatch):
    FakeSerial.reset(execution_chunks=[b"OKpartial stdout"])
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "while True: pass",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_completion_unconfirmed"
    assert result["execution_acknowledged"] is True
    assert result["stdout_eot_observed"] is False
    assert result["stderr_eot_observed"] is False
    assert result["raw_repl_prompt_observed"] is False
    assert result["execution_completed"] is False
    assert result["stdout"] == "partial stdout"
    assert result["stderr"] == ""


def test_raw_repl_preserves_partial_stderr_without_final_frame(monkeypatch):
    FakeSerial.reset(execution_chunks=[b"OKout\x04partial error"])
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "raise RuntimeError('boom')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_completion_unconfirmed"
    assert result["execution_acknowledged"] is True
    assert result["stdout_eot_observed"] is True
    assert result["stderr_eot_observed"] is False
    assert result["raw_repl_prompt_observed"] is False
    assert result["execution_completed"] is False
    assert result["stdout"] == "out"
    assert result["stderr"] == "partial error"


def test_raw_repl_second_eot_without_prompt_is_not_complete(monkeypatch):
    FakeSerial.reset(execution_chunks=[b"OKout\x04error\x04"])
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "raise RuntimeError('boom')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_completion_unconfirmed"
    assert result["execution_acknowledged"] is True
    assert result["stdout_eot_observed"] is True
    assert result["stderr_eot_observed"] is True
    assert result["raw_repl_prompt_observed"] is False
    assert result["execution_completed"] is False
    assert result["stdout"] == "out"
    assert result["stderr"] == "error"


def test_raw_repl_stderr_starting_with_prompt_character_is_not_truncated(
    monkeypatch,
):
    FakeSerial.reset(
        execution_chunks=[b"OKout\x04>", b"diagnostic\x04>"],
    )
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "raise RuntimeError('boom')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_runtime_error"
    assert result["execution_acknowledged"] is True
    assert result["execution_completed"] is True
    assert result["stdout"] == "out"
    assert result["stderr"] == ">diagnostic"


def test_raw_repl_missing_protocol_ack_is_execute_failure(monkeypatch):
    FakeSerial.reset(execution_chunks=[b"noiseOKout\x04\x04>"])
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('out')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_execute_failed"
    assert result["execution_acknowledged"] is False
    assert result["stdout_eot_observed"] is False
    assert result["stderr_eot_observed"] is False
    assert result["raw_repl_prompt_observed"] is False
    assert result["execution_completed"] is False
    assert "noiseOKout" in result["stderr"]


def test_raw_repl_complete_runtime_error_is_completed_but_not_ok(monkeypatch):
    FakeSerial.reset(
        execution_chunks=[b"OKout\x04Traceback: boom\x04>"],
    )
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "raise RuntimeError('boom')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_runtime_error"
    assert result["execution_acknowledged"] is True
    assert result["execution_completed"] is True
    assert result["stdout"] == "out"
    assert result["stderr"] == "Traceback: boom"


def test_raw_repl_accepts_complete_payload_delivered_in_one_read(monkeypatch):
    FakeSerial.reset(execution_chunks=[b"OKone chunk\x04\x04>"])
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('one chunk')",
        timeout_ms=100,
    )

    assert result["ok"] is True
    assert result["execution_acknowledged"] is True
    assert result["execution_completed"] is True
    assert result["stdout"] == "one chunk"


def test_raw_repl_accepts_protocol_tokens_split_across_reads(monkeypatch):
    FakeSerial.reset(
        execution_chunks=[b"O", b"Ksplit", b"\x04", b"\x04", b">"],
    )
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('split')",
        timeout_ms=100,
    )

    assert result["ok"] is True
    assert result["execution_acknowledged"] is True
    assert result["stdout_eot_observed"] is True
    assert result["stderr_eot_observed"] is True
    assert result["raw_repl_prompt_observed"] is True
    assert result["execution_completed"] is True
    assert result["stdout"] == "split"


def test_raw_repl_does_not_send_ctrl_b_when_entry_failed(monkeypatch):
    FakeSerial.reset(entry_chunks=[])
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('never sent')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_enter_failed"
    assert b"\x02" not in FakeSerial.instances[0].writes
    assert FakeSerial.instances[0].closed is True


@pytest.mark.parametrize(
    "entry_chunks",
    [
        [b"raw REPL; CTRL-B to exit garbage\r\n>"],
        [b"raw REPL; CTRL-B to exit\r\n>>>"],
    ],
)
def test_raw_repl_requires_exact_entry_prompt(monkeypatch, entry_chunks):
    FakeSerial.reset(entry_chunks=entry_chunks)
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('never sent')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_enter_failed"
    assert result["raw_repl_entered"] is False
    assert b"\x02" not in FakeSerial.instances[0].writes
    assert not any(write.endswith(b"\x04") for write in FakeSerial.instances[0].writes)


def test_raw_repl_cleanup_failure_preserves_completed_output(monkeypatch):
    FakeSerial.reset(
        execution_chunks=[b"OKhello\r\n\x04\x04>"],
        fail_on="close",
    )
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('hello')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_io_error"
    assert result["failure_stage"] == "cleanup"
    assert result["execution_completed"] is True
    assert result["stdout"] == "hello\r\n"
    assert result["serial_cleanup_completed"] is False
    assert any(item.startswith("close:") for item in result["cleanup_errors"])


def test_raw_repl_runtime_error_and_cleanup_failure_keep_error_kinds_separate(
    monkeypatch,
):
    FakeSerial.reset(
        execution_chunks=[b"OKout\x04Traceback: boom\x04>"],
        fail_on="close",
    )
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "raise RuntimeError('boom')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_io_error"
    assert result["operation_error_kind"] == "raw_repl_runtime_error"
    assert "protocol_error_kind" not in result
    assert result["execution_completed"] is True
    assert result["stderr"] == "Traceback: boom"


def test_raw_repl_exit_failure_preserves_completed_output(monkeypatch):
    FakeSerial.reset(
        execution_chunks=[b"OKhello\r\n\x04\x04>"],
        fail_on="raw_repl_exit",
    )
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('hello')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_io_error"
    assert result["failure_stage"] == "raw_repl_exit"
    assert result["execution_completed"] is True
    assert result["stdout"] == "hello\r\n"
    assert result["raw_repl_exit_sent"] is False
    assert result["raw_repl_exit_write_count"] == 0
    assert result["raw_repl_exit_confirmed"] is False
    assert result["cleanup_completed"] is True
    assert result["serial_cleanup_completed"] is True
    assert FakeSerial.instances[0].closed is True


def test_raw_repl_short_code_write_is_an_io_error(monkeypatch):
    FakeSerial.reset(fail_on="code_short_write")
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('not fully written')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_io_error"
    assert result["failure_stage"] == "code_write"
    assert result["execution_acknowledged"] is False
    assert result["execution_completed"] is False


def test_raw_repl_short_exit_write_is_not_reported_as_sent(monkeypatch):
    FakeSerial.reset(
        execution_chunks=[b"OKhello\x04\x04>"],
        fail_on="raw_repl_exit_short_write",
    )
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code(
        "COM_TEST",
        "print('hello')",
        timeout_ms=100,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "raw_repl_io_error"
    assert result["failure_stage"] == "raw_repl_exit_write"
    assert result["operation_error_kind"] is None
    assert result["raw_repl_exit_sent"] is False
    assert result["raw_repl_exit_write_count"] == 0
    assert result["raw_repl_exit_confirmed"] is False
    assert result["execution_completed"] is True
    assert result["stdout"] == "hello"


def test_exec_code_returns_tool_metadata(monkeypatch):
    def fake_execute_code(port: str, code: str, timeout_ms: int):
        return {"ok": True, "stdout": "ok\n", "stderr": "", "message": code}

    monkeypatch.setattr(exec_tools, "execute_code", fake_execute_code)

    result = exec_tools.esp_exec_code(port="COM_TEST", code="print('ok')")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_exec_code"
    assert result["tools鍚嶇О"] == "esp_exec_code"
    assert result["port"] == "COM_TEST"


def test_run_remote_file_uses_mpremote(monkeypatch):
    from esp_mcp_toolchain.tools import exec_tools

    def fake_run_remote_file(port: str, remote_path: str, timeout_s: int):
        return {"ok": True, "stdout": "remote ok\n", "stderr": "", "message": remote_path}

    monkeypatch.setattr(exec_tools.mpremote_backend, "run_remote_file", fake_run_remote_file)

    result = exec_tools.esp_run_file(port="COM_TEST", backend="mpremote", path="/main.py", path_type="remote")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_run_file"
    assert result["backend"] == "mpremote"
    assert result["stdout"] == "remote ok\n"


def test_run_remote_file_can_use_raw_repl(monkeypatch):
    from esp_mcp_toolchain.tools import exec_tools

    def fake_exec_code(port: str, backend: str, code: str, capture_ms: int):
        return {"ok": True, "stdout": code, "stderr": ""}

    monkeypatch.setattr(exec_tools, "esp_exec_code", fake_exec_code)

    result = exec_tools.esp_run_file(port="COM_TEST", backend="raw_repl", path="/main.py", path_type="remote")

    assert result["ok"] is True
    assert result["tool_name"] == "esp_run_file"
    assert "open('/main.py')" in result["stdout"]
