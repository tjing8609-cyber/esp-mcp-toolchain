from __future__ import annotations

from esp_mcp_toolchain.tools import reset_tools


class FakeSerial:
    writes: list[bytes] = []
    control_changes: list[tuple[str, bool]] = []

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

    def setDTR(self, state: bool) -> None:
        self.dtr = state
        self.control_changes.append(("dtr", state))

    def setRTS(self, state: bool) -> None:
        self.rts = state
        self.control_changes.append(("rts", state))

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


def test_reset_hard_restarts_app_without_asserting_boot_pin(monkeypatch):
    FakeSerial.control_changes = []
    monkeypatch.setattr(reset_tools, "get_serial_module", lambda: FakeSerialModule)

    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_reset"
    assert result["mode"] == "hard"
    assert FakeSerial.control_changes == [("dtr", False), ("rts", True), ("rts", False)]
    assert "MicroPython" in result["text"]


def test_reset_rejects_unknown_mode():
    result = reset_tools.esp_reset(port="COM_TEST", mode="unknown")

    assert result["ok"] is False
    assert result["error_kind"] == "unsupported_reset_mode"
    assert result["implemented"] is True
