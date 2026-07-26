from __future__ import annotations

import pytest

from esp_mcp_toolchain.backends import pyserial_backend


class LifecycleSerial:
    instances: list["LifecycleSerial"] = []
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
        self.is_open = False
        self.closed = False
        self._dtr_failed = False
        self._rts_failed = False
        self.open_snapshot: dict[str, object] = {}
        type(self).instances.append(self)
        type(self).events.append(("construct", None))

    @classmethod
    def reset(cls, *, fail_on: str | None = None) -> None:
        cls.instances = []
        cls.events = []
        cls.fail_on = fail_on

    @property
    def dtr(self) -> bool:
        return self._dtr

    @dtr.setter
    def dtr(self, value: bool) -> None:
        type(self).events.append(("dtr", value))
        if type(self).fail_on == "dtr_once" and not self._dtr_failed:
            self._dtr_failed = True
            raise OSError("DTR setup failed")
        if type(self).fail_on == "dtr_cleanup" and self.is_open:
            raise OSError("DTR cleanup failed")
        self._dtr = value

    @property
    def rts(self) -> bool:
        return self._rts

    @rts.setter
    def rts(self, value: bool) -> None:
        type(self).events.append(("rts", value))
        if type(self).fail_on == "rts_cleanup" and self.is_open:
            raise OSError("RTS cleanup failed")
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
        self.is_open = True
        if type(self).fail_on == "driver_line_change":
            self._dtr = True
            self._rts = True

    def close(self) -> None:
        type(self).events.append(("close", None))
        if type(self).fail_on == "close":
            raise OSError("close failed")
        self.closed = True
        self.is_open = False


class LifecycleSerialModule:
    Serial = LifecycleSerial


def test_safe_open_configures_before_open_and_reasserts_after_driver_change():
    LifecycleSerial.reset(fail_on="driver_line_change")

    serial_port = pyserial_backend.open_serial_with_inactive_control_lines(
        LifecycleSerialModule,
        "COM_TEST",
        baudrate=230400,
        timeout=0.25,
        write_timeout=0.75,
    )

    assert serial_port.open_snapshot == {
        "port": "COM_TEST",
        "baudrate": 230400,
        "timeout": 0.25,
        "write_timeout": 0.75,
        "rtscts": False,
        "dsrdtr": False,
        "xonxoff": False,
        "dtr": False,
        "rts": False,
    }
    assert serial_port.dtr is False
    assert serial_port.rts is False
    open_index = next(
        index
        for index, event in enumerate(LifecycleSerial.events)
        if event[0] == "open"
    )
    assert LifecycleSerial.events[open_index - 2 : open_index] == [
        ("dtr", False),
        ("rts", False),
    ]
    assert LifecycleSerial.events[open_index + 1 : open_index + 3] == [
        ("dtr", False),
        ("rts", False),
    ]


def test_safe_open_preserves_original_exception_and_reports_lifecycle_stage():
    LifecycleSerial.reset(fail_on="open")

    with pytest.raises(OSError, match="Access is denied") as caught:
        pyserial_backend.open_serial_with_inactive_control_lines(
            LifecycleSerialModule,
            "COM_BUSY",
        )

    details = pyserial_backend.serial_lifecycle_details(caught.value)
    assert details == {
        "stage": "open",
        "serial_constructed": True,
        "open_attempted": True,
        "open_completed": False,
        "cleanup_errors": [],
    }
    assert LifecycleSerial.instances[0].closed is True


def test_safe_open_stops_before_open_when_control_line_setup_fails():
    LifecycleSerial.reset(fail_on="dtr_once")

    with pytest.raises(OSError, match="DTR setup failed") as caught:
        pyserial_backend.open_serial_with_inactive_control_lines(
            LifecycleSerialModule,
            "COM_TEST",
        )

    details = pyserial_backend.serial_lifecycle_details(caught.value)
    assert details is not None
    assert details["stage"] == "configure"
    assert details["open_attempted"] is False
    assert not any(name == "open" for name, _value in LifecycleSerial.events)
    assert LifecycleSerial.instances[0].closed is True


def test_deactivate_and_close_attempts_every_cleanup_step():
    class CleanupSerial:
        def __init__(self):
            self.events: list[tuple[str, object]] = []

        @property
        def dtr(self) -> bool:
            return True

        @dtr.setter
        def dtr(self, value: bool) -> None:
            self.events.append(("dtr", value))
            raise OSError("DTR cleanup failed")

        @property
        def rts(self) -> bool:
            return True

        @rts.setter
        def rts(self, value: bool) -> None:
            self.events.append(("rts", value))
            raise OSError("RTS cleanup failed")

        def close(self) -> None:
            self.events.append(("close", None))
            raise OSError("close failed")

    serial_port = CleanupSerial()

    errors = pyserial_backend.deactivate_and_close_serial(serial_port)

    assert serial_port.events == [
        ("dtr", False),
        ("rts", False),
        ("close", None),
    ]
    assert [item.split(":", 1)[0] for item in errors] == [
        "dtr_cleanup",
        "rts_cleanup",
        "close",
    ]


def test_port_probe_reports_safe_success_and_cleanup(monkeypatch):
    LifecycleSerial.reset()
    monkeypatch.setattr(
        pyserial_backend,
        "get_serial_module",
        lambda: LifecycleSerialModule,
    )

    result = pyserial_backend.probe_serial_port("COM_TEST")

    assert result["available"] is True
    assert result["control_lines_preconfigured"] is True
    assert result["physical_reset_excluded"] is False
    assert result["cleanup_completed"] is True
    assert LifecycleSerial.instances[0].closed is True


def test_port_probe_keeps_busy_contract_and_original_message(monkeypatch):
    LifecycleSerial.reset(fail_on="open")
    monkeypatch.setattr(
        pyserial_backend,
        "get_serial_module",
        lambda: LifecycleSerialModule,
    )

    result = pyserial_backend.probe_serial_port("COM_BUSY")
    legacy = pyserial_backend.port_can_open("COM_BUSY")

    assert result["available"] is False
    assert result["busy"] is True
    assert "Access is denied" in result["message"]
    assert result["physical_reset_excluded"] is False
    assert legacy[0] is False
    assert legacy[1] is True
    assert "Access is denied" in legacy[2]


def test_port_probe_does_not_report_available_when_close_fails(monkeypatch):
    LifecycleSerial.reset(fail_on="close")
    monkeypatch.setattr(
        pyserial_backend,
        "get_serial_module",
        lambda: LifecycleSerialModule,
    )

    result = pyserial_backend.probe_serial_port("COM_TEST")

    assert result["available"] is False
    assert result["busy"] is False
    assert result["cleanup_completed"] is False
    assert any(
        item.startswith("close:")
        for item in result["cleanup_errors"]
    )
