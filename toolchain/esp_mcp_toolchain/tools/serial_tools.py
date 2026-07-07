from __future__ import annotations

import time
from pathlib import Path

from ..backends.pyserial_backend import get_serial_module
from ..config import get_selected_port
from ..errors import execution_error
from ..paths import logs_dir
from ..utils.time_utils import now_compact, now_iso
from .log_tools import new_run_id, write_event


def esp_serial_capture(
    port: str | None = None,
    baudrate: int = 115200,
    duration_ms: int = 5000,
    stop_on_traceback: bool = True,
    session_name: str = "default",
) -> dict:
    serial_mod = get_serial_module()
    if serial_mod is None:
        return execution_error(
            "pyserial_missing",
            "pyserial is not installed.",
            tool="esp_serial_capture",
            suggested_next_actions=["Install requirements.txt", "Run python -m pip install pyserial"],
        )

    selected_port = port or get_selected_port()
    if not selected_port:
        return execution_error(
            "serial_port_not_selected",
            "No serial port was provided or selected.",
            tool="esp_serial_capture",
            suggested_next_actions=["Run port-list", "Run port-select COMx"],
        )

    run_id = new_run_id("serial")
    raw_dir = logs_dir() / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{session_name}_{now_compact()}.log"

    end_at = time.monotonic() + max(duration_ms, 0) / 1000
    chunks: list[str] = []
    try:
        with serial_mod.Serial(selected_port, baudrate=baudrate, timeout=0.1) as ser:
            while time.monotonic() < end_at:
                data = ser.read(4096)
                if not data:
                    continue
                text = data.decode(errors="replace")
                chunks.append(text)
                if stop_on_traceback and "Traceback (most recent call last)" in text:
                    break
    except Exception as exc:
        return execution_error(
            "serial_capture_failed",
            str(exc),
            tool="esp_serial_capture",
            run_id=run_id,
            suggested_next_actions=["Check port name", "Close other serial monitors", "Run port-status"],
        )

    text = "".join(chunks)
    raw_path.write_text(text, encoding="utf-8")
    write_event(
        "esp_serial_capture",
        "serial",
        f"Captured {len(text)} characters from {selected_port}",
        {
            "port": selected_port,
            "baudrate": baudrate,
            "duration_ms": duration_ms,
            "raw_path": str(raw_path),
            "created_at": now_iso(),
        },
        run_id=run_id,
        source="esp32",
    )
    return {
        "ok": True,
        "run_id": run_id,
        "port": selected_port,
        "baudrate": baudrate,
        "raw_path": str(Path(raw_path)),
        "text": text,
    }


def esp_serial_monitor_start(port: str, baudrate: int = 115200, session_name: str = "default") -> dict:
    return execution_error("not_implemented", "Background serial monitor is not implemented yet.", tool="esp_serial_monitor_start")


def esp_serial_monitor_stop(session_name: str = "default") -> dict:
    return execution_error("not_implemented", "Background serial monitor is not implemented yet.", tool="esp_serial_monitor_stop")


def esp_serial_monitor_status() -> dict:
    return {"ok": True, "monitors": []}

