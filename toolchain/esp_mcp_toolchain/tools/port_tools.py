from __future__ import annotations

from ..backends.pyserial_backend import list_serial_ports, probe_serial_port
from ..config import get_selected_port, set_selected_port
from .log_tools import logged_task


def esp_port_list() -> dict:
    ports, backend_available, message = list_serial_ports()
    return {
        "ok": True,
        "ports": ports,
        "backend_available": backend_available,
        "message": message,
    }


@logged_task(
    task_type="port_select",
    selected_port_arg="port",
    payload_args=("reason",),
)
def esp_port_select(port: str, reason: str = "manual") -> dict:
    set_selected_port(port, reason)
    return {
        "ok": True,
        "selected_port": port,
        "message": f"Selected port {port}",
    }


def esp_port_status() -> dict:
    selected_port = get_selected_port()
    if not selected_port:
        return {
            "ok": True,
            "selected_port": None,
            "available": False,
            "busy": False,
            "backend_available": None,
            "control_lines_preconfigured": False,
            "physical_reset_excluded": True,
            "cleanup_completed": True,
            "cleanup_errors": [],
            "message": "No selected port.",
        }
    probe = probe_serial_port(selected_port)
    return {
        "ok": True,
        "selected_port": selected_port,
        **probe,
    }

