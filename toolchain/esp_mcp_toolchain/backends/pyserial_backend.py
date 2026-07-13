from __future__ import annotations

from typing import Any


def _port_payload(port: Any) -> dict:
    vid = f"{port.vid:04X}" if port.vid is not None else None
    pid = f"{port.pid:04X}" if port.pid is not None else None
    description = port.description or ""
    likely_esp = any(token in description.lower() for token in ("cp210", "ch340", "ch910", "usb jtag", "uart", "esp"))
    return {
        "port": port.device,
        "device_path": port.device,
        "description": description,
        "vid": vid,
        "pid": pid,
        "serial_number": getattr(port, "serial_number", None),
        "location": getattr(port, "location", None),
        "manufacturer": getattr(port, "manufacturer", None),
        "product": getattr(port, "product", None),
        "interface": getattr(port, "interface", None),
        "hwid": getattr(port, "hwid", None),
        "likely_esp": likely_esp,
    }


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

    ports = [_port_payload(port) for port in list_ports.comports()]
    return ports, True, "ok"


def describe_serial_port(port_name: str) -> dict:
    ports, available, _message = list_serial_ports()
    if available:
        for port in ports:
            if str(port.get("port", "")).casefold() == port_name.casefold():
                return port
    return {
        "port": port_name,
        "device_path": port_name,
        "description": "",
        "vid": None,
        "pid": None,
        "serial_number": None,
        "location": None,
        "manufacturer": None,
        "product": None,
        "interface": None,
        "hwid": None,
        "likely_esp": False,
    }


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
