from __future__ import annotations

import time
from typing import Literal

from ..backends.pyserial_backend import get_serial_module
from ..config import get_selected_port
from ..errors import execution_error
from .log_tools import logged_task


@logged_task(task_type="reset", selected_port_arg="port", payload_args=("mode",))
def esp_reset(port: str | None = None, mode: Literal["soft", "hard"] = "soft") -> dict:
    if mode not in {"soft", "hard"}:
        return execution_error(
            "unsupported_reset_mode",
            f"Unsupported reset mode: {mode}",
            tool="esp_reset",
            implemented=True,
            suggested_next_actions=["Use mode=soft for MicroPython", "Use mode=hard to restart the running firmware"],
        )

    serial_mod = get_serial_module()
    if serial_mod is None:
        return execution_error(
            "pyserial_missing",
            "pyserial is not installed.",
            tool="esp_reset",
            suggested_next_actions=["Install requirements.txt", "Run python -m pip install pyserial"],
        )

    selected_port = port or get_selected_port()
    if not selected_port:
        return execution_error(
            "serial_port_not_selected",
            "No serial port was provided or selected.",
            tool="esp_reset",
            suggested_next_actions=["Run esp_port_list", "Run esp_port_select with the confirmed board port"],
        )

    chunks: list[str] = []
    try:
        with serial_mod.Serial(selected_port, baudrate=115200, timeout=0.1) as ser:
            if mode == "soft":
                ser.dtr = False
                ser.rts = False
                ser.write(b"\x03")
                time.sleep(0.1)
                ser.write(b"\x04")
            else:
                # Match esptool's control-line semantics while keeping IO0 high.
                ser.setDTR(False)
                ser.setRTS(True)
                time.sleep(0.1)
                ser.setRTS(False)
            end_at = time.monotonic() + 2.0
            while time.monotonic() < end_at:
                data = ser.read(4096)
                if data:
                    chunks.append(data.decode("utf-8", errors="replace"))
    except Exception as exc:
        return execution_error(
            "reset_failed",
            str(exc),
            tool="esp_reset",
            port=selected_port,
            mode=mode,
            suggested_next_actions=["Check port name", "Close other serial monitors", "Run esp_port_status"],
        )

    text = "".join(chunks)
    message = "Sent MicroPython soft reset over serial." if mode == "soft" else "Restarted firmware with a hardware reset."
    return {
        "ok": True,
        "tool": "esp_reset",
        "tool_name": "esp_reset",
        "tools鍚嶇О": "esp_reset",
        "implemented": True,
        "port": selected_port,
        "mode": mode,
        "text": text,
        "message": message,
        "data": {"text": text},
    }
