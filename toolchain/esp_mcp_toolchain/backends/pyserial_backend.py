from __future__ import annotations

from typing import Any


class SerialLifecycleError(RuntimeError):
    def __init__(
        self,
        stage: str,
        cause: Exception,
        *,
        serial_constructed: bool,
        open_attempted: bool,
        open_completed: bool,
        cleanup_errors: list[str],
    ):
        message = f"Serial lifecycle failed during {stage}: {type(cause).__name__}: {cause}"
        if cleanup_errors:
            message += f". Cleanup: {'; '.join(cleanup_errors)}"
        super().__init__(message)
        self.stage = stage
        self.cause = cause
        self.serial_constructed = serial_constructed
        self.open_attempted = open_attempted
        self.open_completed = open_completed
        self.cleanup_errors = cleanup_errors


def serial_lifecycle_details(exc: BaseException) -> dict[str, Any] | None:
    stage = getattr(exc, "serial_failure_stage", None)
    if stage is None and isinstance(exc, SerialLifecycleError):
        stage = exc.stage
    if stage is None:
        return None
    return {
        "stage": str(stage),
        "serial_constructed": bool(
            getattr(exc, "serial_constructed", False)
        ),
        "open_attempted": bool(
            getattr(exc, "serial_open_attempted", getattr(exc, "open_attempted", False))
        ),
        "open_completed": bool(
            getattr(exc, "serial_open_completed", getattr(exc, "open_completed", False))
        ),
        "cleanup_errors": list(
            getattr(exc, "serial_cleanup_errors", getattr(exc, "cleanup_errors", []))
        ),
    }


def _annotate_serial_lifecycle_error(
    exc: Exception,
    *,
    stage: str,
    serial_constructed: bool,
    open_attempted: bool,
    open_completed: bool,
    cleanup_errors: list[str],
) -> bool:
    try:
        exc.serial_failure_stage = stage  # type: ignore[attr-defined]
        exc.serial_constructed = serial_constructed  # type: ignore[attr-defined]
        exc.serial_open_attempted = open_attempted  # type: ignore[attr-defined]
        exc.serial_open_completed = open_completed  # type: ignore[attr-defined]
        exc.serial_cleanup_errors = list(cleanup_errors)  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        return False
    return True


def configure_serial_with_inactive_control_lines(
    serial_port: Any,
    *,
    port: str,
    baudrate: int,
    timeout: float,
    write_timeout: float = 1.0,
) -> None:
    serial_port.port = port
    serial_port.baudrate = baudrate
    serial_port.timeout = timeout
    serial_port.write_timeout = write_timeout
    serial_port.rtscts = False
    serial_port.dsrdtr = False
    serial_port.xonxoff = False
    serial_port.dtr = False
    serial_port.rts = False


def reassert_inactive_control_lines(serial_port: Any) -> None:
    serial_port.dtr = False
    serial_port.rts = False


def deactivate_and_close_serial(serial_port: Any | None) -> list[str]:
    if serial_port is None:
        return []
    cleanup_errors: list[str] = []
    try:
        serial_port.dtr = False
    except Exception as exc:
        cleanup_errors.append(f"dtr_cleanup: {type(exc).__name__}: {exc}")
    try:
        serial_port.rts = False
    except Exception as exc:
        cleanup_errors.append(f"rts_cleanup: {type(exc).__name__}: {exc}")
    try:
        serial_port.close()
    except Exception as exc:
        cleanup_errors.append(f"close: {type(exc).__name__}: {exc}")
    return cleanup_errors


def open_serial_with_inactive_control_lines(
    serial_module: Any,
    port: str,
    *,
    baudrate: int = 115200,
    timeout: float = 0.1,
    write_timeout: float = 1.0,
) -> Any:
    serial_port: Any | None = None
    stage = "construct"
    open_attempted = False
    open_completed = False
    try:
        serial_port = serial_module.Serial()
        stage = "configure"
        configure_serial_with_inactive_control_lines(
            serial_port,
            port=port,
            baudrate=baudrate,
            timeout=timeout,
            write_timeout=write_timeout,
        )
        stage = "open"
        open_attempted = True
        serial_port.open()
        open_completed = True
        stage = "post_open_control_lines"
        reassert_inactive_control_lines(serial_port)
        return serial_port
    except Exception as exc:
        cleanup_errors = deactivate_and_close_serial(serial_port)
        annotated = _annotate_serial_lifecycle_error(
            exc,
            stage=stage,
            serial_constructed=serial_port is not None,
            open_attempted=open_attempted,
            open_completed=open_completed,
            cleanup_errors=cleanup_errors,
        )
        if annotated:
            raise
        raise SerialLifecycleError(
            stage,
            exc,
            serial_constructed=serial_port is not None,
            open_attempted=open_attempted,
            open_completed=open_completed,
            cleanup_errors=cleanup_errors,
        ) from exc
    except BaseException:
        deactivate_and_close_serial(serial_port)
        raise


def _port_payload(port: Any) -> dict:
    vid = f"{port.vid:04X}" if port.vid is not None else None
    pid = f"{port.pid:04X}" if port.pid is not None else None
    description = port.description or ""
    likely_esp = any(token in description.lower() for token in ("cp210", "ch340", "ch910", "usb jtag", "uart", "esp"))
    return {
        "enumerated": True,
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
        "enumerated": False,
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


def probe_serial_port(port: str) -> dict[str, Any]:
    serial_mod = get_serial_module()
    if serial_mod is None:
        return {
            "available": False,
            "busy": False,
            "message": "pyserial is not installed.",
            "backend_available": False,
            "control_lines_preconfigured": False,
            "physical_reset_excluded": True,
            "cleanup_completed": True,
            "cleanup_errors": [],
        }
    serial_port = None
    try:
        serial_port = open_serial_with_inactive_control_lines(serial_mod, port, timeout=0.1)
    except Exception as exc:
        message = str(exc)
        busy = "access is denied" in message.lower() or "permission" in message.lower()
        lifecycle = serial_lifecycle_details(exc)
        return {
            "available": False,
            "busy": busy,
            "message": message,
            "backend_available": True,
            "control_lines_preconfigured": bool(
                lifecycle
                and lifecycle["stage"] in {"open", "post_open_control_lines"}
            ),
            "physical_reset_excluded": not bool(lifecycle and lifecycle["open_attempted"]),
            "cleanup_completed": not bool(lifecycle and lifecycle["cleanup_errors"]),
            "cleanup_errors": list(lifecycle["cleanup_errors"]) if lifecycle else [],
        }
    cleanup_errors = deactivate_and_close_serial(serial_port)
    if cleanup_errors:
        return {
            "available": False,
            "busy": False,
            "message": f"Serial port opened, but cleanup failed: {'; '.join(cleanup_errors)}",
            "backend_available": True,
            "control_lines_preconfigured": True,
            "physical_reset_excluded": False,
            "cleanup_completed": False,
            "cleanup_errors": cleanup_errors,
        }
    return {
        "available": True,
        "busy": False,
        "message": "ok",
        "backend_available": True,
        "control_lines_preconfigured": True,
        "physical_reset_excluded": False,
        "cleanup_completed": True,
        "cleanup_errors": [],
    }


def port_can_open(port: str) -> tuple[bool, bool, str]:
    result = probe_serial_port(port)
    return bool(result["available"]), bool(result["busy"]), str(result["message"])
