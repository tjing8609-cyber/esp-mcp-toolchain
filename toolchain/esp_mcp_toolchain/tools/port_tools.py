from __future__ import annotations

from ..backends.pyserial_backend import list_serial_ports, port_can_open
from ..config import get_selected_port, set_selected_port
from .log_tools import write_event


def esp_port_list() -> dict:
    ports, backend_available, message = list_serial_ports()
    return {
        "ok": True,
        "ports": ports,
        "backend_available": backend_available,
        "message": message,
    }


def esp_port_select(port: str, reason: str = "manual") -> dict:
    set_selected_port(port, reason)
    write_event("esp_port_select", "info", f"Selected port {port}", {"port": port, "reason": reason})
    return {"ok": True, "selected_port": port}


def esp_port_status() -> dict:
    selected_port = get_selected_port()
    if not selected_port:
        return {
            "ok": True,
            "selected_port": None,
            "available": False,
            "busy": False,
            "message": "No selected port.",
        }
    available, busy, message = port_can_open(selected_port)
    return {
        "ok": True,
        "selected_port": selected_port,
        "available": available,
        "busy": busy,
        "message": message,
    }

