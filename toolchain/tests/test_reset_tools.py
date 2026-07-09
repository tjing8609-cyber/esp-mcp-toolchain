from __future__ import annotations

from esp_mcp_toolchain.tools import reset_tools


class FakeSerial:
    writes: list[bytes] = []

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.dtr = True
        self.rts = True
        self._reads = [b"soft reboot\r\n", b"MicroPython\r\n>>> "]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def read(self, _size: int) -> bytes:
        if self._reads:
            return self._reads.pop(0)
        return b""


class FakeSerialModule:
    Serial = FakeSerial


def test_reset_soft_sends_ctrl_c_ctrl_d(monkeypatch):
    FakeSerial.writes = []
    monkeypatch.setattr(reset_tools, "get_serial_module", lambda: FakeSerialModule)

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_reset"
    assert FakeSerial.writes == [b"\x03", b"\x04"]
    assert "MicroPython" in result["text"]


def test_reset_non_soft_stays_placeholder():
    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is True
    assert result["implemented"] is False
    assert result["tool_name"] == "esp_reset"
