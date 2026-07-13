from __future__ import annotations

import time
from pathlib import Path
import re

from ..backends.pyserial_backend import describe_serial_port, get_serial_module
from ..backends.serial_monitor_backend import MonitorBinding, MonitorConflictError, SERIAL_MONITOR_MANAGER
from ..backends.serial_monitor_store import SerialLogQuotaError, SerialLogStoreError
from ..config import get_selected_port
from ..errors import execution_error
from ..paths import logs_dir
from ..project_context import get_project_context
from ..utils.time_utils import now_compact, now_iso
from .log_tools import new_run_id, write_event


_SESSION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MONITOR_RUN_ID_PATTERN = re.compile(r"^monitor_\d{8}_\d{6}_[0-9a-f]{8}$")


def _validate_session_name(session_name: str, tool: str) -> dict | None:
    if _SESSION_NAME_PATTERN.fullmatch(session_name):
        return None
    return execution_error(
        "invalid_session_name",
        "session_name must be 1-64 ASCII letters, numbers, dots, underscores, or hyphens.",
        tool=tool,
    )


def _validate_monitor_run_id(run_id: str, tool: str) -> dict | None:
    if _MONITOR_RUN_ID_PATTERN.fullmatch(run_id):
        return None
    return execution_error(
        "invalid_monitor_run_id",
        "run_id is not a generated serial monitor identifier.",
        tool=tool,
    )


def esp_serial_capture(
    port: str | None = None,
    baudrate: int = 115200,
    duration_ms: int = 5000,
    stop_on_traceback: bool = True,
    session_name: str = "default",
) -> dict:
    invalid_session = _validate_session_name(session_name, "esp_serial_capture")
    if invalid_session is not None:
        return invalid_session
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


def esp_serial_monitor_start(
    port: str | None = None,
    baudrate: int = 115200,
    session_name: str = "default",
) -> dict:
    tool = "esp_serial_monitor_start"
    invalid_session = _validate_session_name(session_name, tool)
    if invalid_session is not None:
        return invalid_session
    if baudrate <= 0 or baudrate > 10_000_000:
        return execution_error("invalid_baudrate", "baudrate must be between 1 and 10000000.", tool=tool)
    serial_mod = get_serial_module()
    if serial_mod is None:
        return execution_error(
            "pyserial_missing",
            "pyserial is not installed.",
            tool=tool,
            suggested_next_actions=["Install requirements.txt", "Run python -m pip install pyserial"],
        )
    selected_port = port or get_selected_port()
    if not selected_port:
        return execution_error(
            "serial_port_not_selected",
            "No serial port was provided or selected.",
            tool=tool,
            suggested_next_actions=["Run esp_port_list", "Run esp_port_select with an enumerated port"],
        )

    context = get_project_context()
    run_id = new_run_id("monitor")
    binding = MonitorBinding(
        run_id=run_id,
        project_id=context["project_id"],
        project_dir=Path(context["project_dir"]),
        log_root=Path(context["project_dir"]) / "logs",
        session_name=session_name,
        port=selected_port,
        port_identity=describe_serial_port(selected_port),
        baudrate=baudrate,
    )
    try:
        session = SERIAL_MONITOR_MANAGER.start(binding, serial_mod)
    except SerialLogQuotaError as exc:
        return execution_error("serial_log_quota_exceeded", str(exc), tool=tool)
    except MonitorConflictError as exc:
        return execution_error(exc.error_kind, str(exc), tool=tool)
    except (OSError, RuntimeError) as exc:
        return execution_error("serial_monitor_start_failed", str(exc), tool=tool)
    status = session.status()
    if status["state"] == "FAILED":
        return execution_error(
            status.get("last_error", {}).get("error_kind", "serial_monitor_start_failed"),
            status.get("last_error", {}).get("message", "Serial monitor failed during startup."),
            tool=tool,
            monitor=status,
        )
    return {
        "ok": True,
        "run_id": run_id,
        "state": status["state"],
        "monitor": status,
    }


def esp_serial_monitor_stop(run_id: str, timeout_ms: int = 5000) -> dict:
    tool = "esp_serial_monitor_stop"
    invalid_run_id = _validate_monitor_run_id(run_id, tool)
    if invalid_run_id is not None:
        return invalid_run_id
    if timeout_ms < 0 or timeout_ms > 30_000:
        return execution_error("invalid_timeout", "timeout_ms must be between 0 and 30000.", tool=tool)
    context = get_project_context()
    status = SERIAL_MONITOR_MANAGER.stop(run_id, context["project_id"], timeout_ms / 1000)
    if status is None:
        persisted = SERIAL_MONITOR_MANAGER.persisted_status(Path(context["project_dir"]) / "logs", run_id)
        if persisted is not None and persisted.get("project_id") == context["project_id"]:
            return {"ok": True, "run_id": run_id, "already_terminal": True, "monitor": persisted}
        return execution_error("monitor_run_not_found", f"No monitor run {run_id} exists in the active project.", tool=tool)
    if status.get("worker_alive"):
        return execution_error(
            "monitor_cleanup_timeout",
            "Monitor cleanup did not finish within the requested timeout.",
            tool=tool,
            monitor=status,
        )
    return {"ok": True, "run_id": run_id, "monitor": status}


def esp_serial_monitor_status(run_id: str | None = None) -> dict:
    if run_id is not None:
        invalid_run_id = _validate_monitor_run_id(run_id, "esp_serial_monitor_status")
        if invalid_run_id is not None:
            return invalid_run_id
    context = get_project_context()
    monitors = SERIAL_MONITOR_MANAGER.status(context["project_id"], run_id)
    if run_id and not monitors:
        persisted = SERIAL_MONITOR_MANAGER.persisted_status(Path(context["project_dir"]) / "logs", run_id)
        if persisted is not None and persisted.get("project_id") == context["project_id"]:
            monitors = [persisted]
    return {"ok": True, "monitors": monitors}


def esp_serial_monitor_read(
    run_id: str,
    after_seq: int | None = None,
    max_bytes: int = 65_536,
    wait_ms: int = 0,
    representation: str = "text",
) -> dict:
    tool = "esp_serial_monitor_read"
    if after_seq is not None and after_seq < 0:
        return execution_error("invalid_cursor", "after_seq must be non-negative or null.", tool=tool)
    if max_bytes < 4096 or max_bytes > 65_536:
        return execution_error("invalid_max_bytes", "max_bytes must be between 4096 and 65536.", tool=tool)
    if wait_ms < 0 or wait_ms > 30_000:
        return execution_error("invalid_wait", "wait_ms must be between 0 and 30000.", tool=tool)
    if representation not in {"text", "base64", "both"}:
        return execution_error(
            "invalid_representation",
            "representation must be text, base64, or both.",
            tool=tool,
        )
    invalid_run_id = _validate_monitor_run_id(run_id, tool)
    if invalid_run_id is not None:
        return invalid_run_id
    context = get_project_context()
    try:
        result = SERIAL_MONITOR_MANAGER.read(
            project_id=context["project_id"],
            log_root=Path(context["project_dir"]) / "logs",
            run_id=run_id,
            after_seq=after_seq,
            max_bytes=max_bytes,
            wait_ms=wait_ms,
            representation=representation,
        )
    except FileNotFoundError as exc:
        return execution_error("monitor_run_not_found", str(exc), tool=tool)
    except SerialLogStoreError as exc:
        return execution_error("monitor_log_invalid", str(exc), tool=tool)
    except OSError as exc:
        return execution_error("monitor_log_read_failed", str(exc), tool=tool)
    except (KeyError, TypeError, ValueError) as exc:
        return execution_error("monitor_log_invalid", str(exc), tool=tool)
    return {"ok": True, **result}
