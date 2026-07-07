from __future__ import annotations

from typing import Any


def get_serial_module() -> Any | None:
    try:
        import serial  # type: ignore
    except ImportError:
        return None
    return serial


def list_serial_ports() -> tuple[list[dict], bool, str]:
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError:
        return [], False, "pyserial is not installed."

    ports = []
    for port in list_ports.comports():
        vid = f"{port.vid:04X}" if port.vid is not None else None
        pid = f"{port.pid:04X}" if port.pid is not None else None
        description = port.description or ""
        likely_esp = any(token in description.lower() for token in ("cp210", "ch340", "usb jtag", "uart", "esp"))
        ports.append(
            {
                "port": port.device,
                "description": description,
                "vid": vid,
                "pid": pid,
                "likely_esp": likely_esp,
            }
        )
    return ports, True, "ok"


def port_can_open(port: str) -> tuple[bool, bool, str]:
    serial_mod = get_serial_module()
    if serial_mod is None:
        return False, False, "pyserial is not installed."
    try:
        with serial_mod.Serial(port, timeout=0.1):
            return True, False, "ok"
    except Exception as exc:
        message = str(exc)
        busy = "access is denied" in message.lower() or "permission" in message.lower()
        return False, busy, message

