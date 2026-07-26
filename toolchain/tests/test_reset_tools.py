from __future__ import annotations

from esp_mcp_toolchain.tools import reset_tools


class FakeSerial:
    writes: list[bytes] = []
    control_changes: list[tuple[str, bool]] = []
    instances: list["FakeSerial"] = []

    def __init__(self):
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.rtscts = True
        self.dsrdtr = True
        self.xonxoff = True
        self._dtr = True
        self._rts = True
        self._action_started = False
        self.is_open = False
        self._reads = [b"soft reboot\r\n", b"MicroPython\r\n>>> "]
        type(self).instances.append(self)

    @property
    def dtr(self) -> bool:
        return self._dtr

    @dtr.setter
    def dtr(self, value: bool) -> None:
        self._dtr = value

    @property
    def rts(self) -> bool:
        return self._rts

    @rts.setter
    def rts(self, value: bool) -> None:
        self._rts = value
        if value is True:
            self._action_started = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def write(self, data: bytes) -> int:
        self._action_started = True
        self.writes.append(data)
        return len(data)

    def setDTR(self, state: bool) -> None:
        self.dtr = state
        self.control_changes.append(("dtr", state))

    def setRTS(self, state: bool) -> None:
        self.rts = state
        self.control_changes.append(("rts", state))

    def read(self, _size: int) -> bytes:
        if not self._action_started:
            return b""
        if self._reads:
            return self._reads.pop(0)
        return b""


class FakeSerialModule:
    Serial = FakeSerial


def prepare_reset(monkeypatch) -> None:
    FakeSerial.writes = []
    FakeSerial.instances = []
    monkeypatch.setattr(reset_tools, "get_serial_module", lambda: FakeSerialModule)
    monkeypatch.setattr(reset_tools, "_sleep", lambda _seconds: None)
    clock = [0.0]

    def monotonic() -> float:
        value = clock[0]
        clock[0] += 0.5
        return value

    monkeypatch.setattr(reset_tools, "_monotonic", monotonic)


def test_reset_soft_sends_ctrl_c_ctrl_d(monkeypatch):
    prepare_reset(monkeypatch)

    result = reset_tools.esp_reset(port="COM_TEST", mode="soft")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_reset"
    assert FakeSerial.writes == [b"\x03", b"\x04"]
    assert "MicroPython" in result["text"]


def test_reset_hard_restarts_app_without_asserting_boot_pin(monkeypatch):
    prepare_reset(monkeypatch)

    result = reset_tools.esp_reset(port="COM_TEST", mode="hard")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_reset"
    assert result["mode"] == "hard"
    assert result["hard_reset_pulse_started"] is True
    assert result["hard_reset_pulse_completed"] is True
    assert result["reset_command_sent"] is False
    assert FakeSerial.instances[0].dtr is False
    assert FakeSerial.instances[0].rts is False
    assert "MicroPython" in result["text"]


def test_reset_rejects_unknown_mode():
    result = reset_tools.esp_reset(port="COM_TEST", mode="unknown")

    assert result["ok"] is False
    assert result["error_kind"] == "unsupported_reset_mode"
    assert result["implemented"] is True
