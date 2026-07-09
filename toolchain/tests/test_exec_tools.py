from __future__ import annotations

from esp_mcp_toolchain.backends import raw_repl_backend
from esp_mcp_toolchain.tools import exec_tools


class FakeSerial:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.dtr = True
        self.rts = True
        self.closed = False
        self._reads: list[bytes] = []
        self.writes: list[bytes] = []

    def reset_input_buffer(self) -> None:
        self._reads.clear()

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        if data == b"\x01":
            self._reads.append(b"raw REPL; CTRL-B to exit\r\n>")
        elif data.endswith(b"\x04"):
            self._reads.append(b"OKhello\r\n\x04")
            self._reads.append(b"\x04>")
        return len(data)

    def read(self, _size: int) -> bytes:
        if self._reads:
            return self._reads.pop(0)
        return b""

    def close(self) -> None:
        self.closed = True


class FakeSerialModule:
    Serial = FakeSerial


def test_raw_repl_execute_code_parses_stdout(monkeypatch):
    monkeypatch.setattr(raw_repl_backend, "get_serial_module", lambda: FakeSerialModule)

    result = raw_repl_backend.execute_code("COM_TEST", "print('hello')")

    assert result["ok"] is True
    assert result["stdout"] == "hello\r\n"
    assert result["stderr"] == ""


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
