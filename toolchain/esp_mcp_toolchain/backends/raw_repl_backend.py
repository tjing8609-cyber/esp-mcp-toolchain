from __future__ import annotations

import time
from typing import Any

from .pyserial_backend import (
    deactivate_and_close_serial,
    get_serial_module,
    open_serial_with_inactive_control_lines,
    serial_lifecycle_details,
)


RAW_REPL_PROMPT = b"raw REPL; CTRL-B to exit\r\n>"


def _read_until(ser: Any, markers: tuple[bytes, ...], timeout_s: float) -> bytes:
    deadline = time.monotonic() + max(timeout_s, 0)
    buffer = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(4096)
        if chunk:
            buffer.extend(chunk)
            if any(marker in buffer for marker in markers):
                return bytes(buffer)
        else:
            time.sleep(0.01)
    return bytes(buffer)


def _write_exact(ser: Any, data: bytes) -> None:
    written = ser.write(data)
    if written != len(data):
        raise OSError(
            f"Serial short write: expected {len(data)} bytes, wrote {written}."
        )


def _execution_frame_is_decided(payload: bytes) -> bool:
    if len(payload) < 2:
        return False
    if not payload.startswith(b"OK"):
        return True
    stdout_end = payload.find(b"\x04", 2)
    if stdout_end < 0:
        return False
    stderr_end = payload.find(b"\x04", stdout_end + 1)
    if stderr_end < 0:
        return False
    return len(payload) > stderr_end + 1


def _read_execution_frame(ser: Any, timeout_s: float) -> bytes:
    deadline = time.monotonic() + timeout_s
    buffer = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(4096)
        if chunk:
            buffer.extend(chunk)
            if _execution_frame_is_decided(bytes(buffer)):
                break
        else:
            time.sleep(0.01)
    return bytes(buffer)


def interrupt_program(
    port: str,
    *,
    baudrate: int = 115200,
    timeout_ms: int = 1500,
) -> dict[str, Any]:
    """Send Ctrl-C without issuing a reset command; physical reset cannot be excluded."""
    serial_mod = get_serial_module()
    if serial_mod is None:
        return {
            "ok": False,
            "error_kind": "pyserial_missing",
            "message": "pyserial is not installed.",
            "interrupt_sent": False,
            "stop_confirmed": False,
            "output": "",
        }
    if baudrate <= 0 or baudrate > 10_000_000:
        return {
            "ok": False,
            "error_kind": "invalid_baudrate",
            "message": "baudrate must be between 1 and 10000000.",
            "interrupt_sent": False,
            "stop_confirmed": False,
            "output": "",
        }
    if timeout_ms < 100 or timeout_ms > 30_000:
        return {
            "ok": False,
            "error_kind": "invalid_timeout",
            "message": "timeout_ms must be between 100 and 30000.",
            "interrupt_sent": False,
            "stop_confirmed": False,
            "output": "",
        }

    interrupt_write_count = 0
    ser: Any | None = None
    output_bytes = b""
    pre_action_bytes = b""
    operation_error: Exception | None = None
    cleanup_errors: list[str] = []
    control_lines_preconfigured = False
    open_attempted = False
    failure_stage: str | None = None
    operation_stage = "serial_open"
    try:
        open_attempted = True
        ser = open_serial_with_inactive_control_lines(
            serial_mod,
            port,
            baudrate=baudrate,
            timeout=0.1,
            write_timeout=1.0,
        )
        control_lines_preconfigured = True
        operation_stage = "pre_action_read"
        pre_action_bytes = _read_until(ser, (), 0.1)
        operation_stage = "interrupt_write"
        _write_exact(ser, b"\x03")
        interrupt_write_count = 1
        time.sleep(0.05)
        _write_exact(ser, b"\x03")
        interrupt_write_count = 2
        operation_stage = "interrupt_response_read"
        output_bytes = _read_until(
            ser,
            (b">>>",),
            timeout_ms / 1000,
        )
    except Exception as exc:
        operation_error = exc
        failure_stage = "io"
        lifecycle = serial_lifecycle_details(exc)
        if lifecycle is not None:
            open_attempted = lifecycle["open_attempted"]
            failure_stage = lifecycle["stage"]
            control_lines_preconfigured = lifecycle["stage"] in {
                "open",
                "post_open_control_lines",
            }
            cleanup_errors.extend(lifecycle["cleanup_errors"])
        else:
            failure_stage = operation_stage
    finally:
        if ser is not None:
            cleanup_errors.extend(deactivate_and_close_serial(ser))

    pre_action_output = pre_action_bytes.decode("utf-8", errors="replace")
    output = output_bytes.decode("utf-8", errors="replace")
    observed_keyboard_interrupt = "KeyboardInterrupt" in output
    observed_prompt = ">>>" in output
    stop_confirmed = observed_prompt
    if operation_error is not None or cleanup_errors:
        if operation_error is not None:
            message = f"{type(operation_error).__name__}: {operation_error}"
        else:
            message = "Program stop output was read, but serial cleanup failed."
            failure_stage = "cleanup"
        if cleanup_errors:
            message += f" Cleanup: {'; '.join(cleanup_errors)}"
        return {
            "ok": False,
            "error_kind": "program_stop_io_error",
            "message": message,
            "interrupt_sent": interrupt_write_count > 0,
            "interrupt_write_count": interrupt_write_count,
            "stop_confirmed": stop_confirmed,
            "observed_keyboard_interrupt": observed_keyboard_interrupt,
            "observed_prompt": observed_prompt,
            "output": output,
            "pre_action_output": pre_action_output,
            "reset_command_sent": False,
            "physical_reset_excluded": not open_attempted,
            "control_lines_preconfigured": control_lines_preconfigured,
            "failure_stage": failure_stage,
            "cleanup_completed": not cleanup_errors,
            "cleanup_errors": cleanup_errors,
            "recoverable": True,
        }

    result: dict[str, Any] = {
        "ok": stop_confirmed,
        "interrupt_sent": interrupt_write_count > 0,
        "interrupt_write_count": interrupt_write_count,
        "stop_confirmed": stop_confirmed,
        "observed_keyboard_interrupt": observed_keyboard_interrupt,
        "observed_prompt": observed_prompt,
        "output": output,
        "pre_action_output": pre_action_output,
        "reset_command_sent": False,
        "physical_reset_excluded": False,
        "control_lines_preconfigured": control_lines_preconfigured,
        "cleanup_completed": True,
        "cleanup_errors": [],
        "recoverable": not stop_confirmed,
        "message": (
            "MicroPython program interruption was confirmed."
            if stop_confirmed
            else (
                "KeyboardInterrupt was observed, but no MicroPython prompt confirmed that execution stopped."
                if observed_keyboard_interrupt
                else "Ctrl-C was sent, but no MicroPython prompt confirmed that execution stopped."
            )
        ),
    }
    if pre_action_output:
        result["message"] += " Pre-action serial output was observed separately."
    if not stop_confirmed:
        result["error_kind"] = "program_stop_unconfirmed"
    return result


def _parse_execution_payload(payload: bytes) -> dict[str, Any]:
    execution_acknowledged = payload.startswith(b"OK")
    result: dict[str, Any] = {
        "ok": False,
        "execution_acknowledged": execution_acknowledged,
        "stdout_eot_observed": False,
        "stderr_eot_observed": False,
        "raw_repl_prompt_observed": False,
        "execution_completed": False,
        "stdout": "",
        "stderr": "",
        "protocol_tail": "",
    }
    if not execution_acknowledged:
        result.update(
            {
                "error_kind": "raw_repl_execute_failed",
                "message": "MicroPython raw REPL did not acknowledge the code.",
                "stderr": payload.decode("utf-8", errors="replace"),
            }
        )
        return result

    body = payload[2:]
    stdout_end = body.find(b"\x04")
    if stdout_end < 0:
        result.update(
            {
                "error_kind": "raw_repl_completion_unconfirmed",
                "message": (
                    "MicroPython raw REPL acknowledged the code, but did not "
                    "confirm a complete termination frame before the read "
                    "deadline."
                ),
                "stdout": body.decode("utf-8", errors="replace"),
            }
        )
        return result

    result["stdout_eot_observed"] = True
    result["stdout"] = body[:stdout_end].decode("utf-8", errors="replace")
    stderr_and_prompt = body[stdout_end + 1 :]
    stderr_end = stderr_and_prompt.find(b"\x04")
    if stderr_end < 0:
        result.update(
            {
                "error_kind": "raw_repl_completion_unconfirmed",
                "message": (
                    "MicroPython raw REPL acknowledged the code, but did not "
                    "confirm a complete termination frame before the read "
                    "deadline."
                ),
                "stderr": stderr_and_prompt.decode(
                    "utf-8",
                    errors="replace",
                ),
            }
        )
        return result

    result["stderr_eot_observed"] = True
    stderr_bytes = stderr_and_prompt[:stderr_end]
    result["stderr"] = stderr_bytes.decode("utf-8", errors="replace")
    after_stderr_eot = stderr_and_prompt[stderr_end + 1 :]
    raw_repl_prompt_observed = after_stderr_eot.startswith(b">")
    result["raw_repl_prompt_observed"] = raw_repl_prompt_observed
    if not raw_repl_prompt_observed:
        result.update(
            {
                "error_kind": "raw_repl_completion_unconfirmed",
                "message": (
                    "MicroPython raw REPL acknowledged the code, but did not "
                    "confirm a complete termination frame before the read "
                    "deadline."
                ),
                "protocol_tail": after_stderr_eot.decode(
                    "utf-8",
                    errors="replace",
                ),
            }
        )
        return result

    result["protocol_tail"] = after_stderr_eot[1:].decode(
        "utf-8",
        errors="replace",
    )
    result["execution_completed"] = True
    if stderr_bytes:
        result.update(
            {
                "error_kind": "raw_repl_runtime_error",
                "message": (
                    "Code execution completed through MicroPython raw REPL "
                    "and returned runtime stderr."
                ),
            }
        )
        return result

    result.update(
        {
            "ok": True,
            "message": "Code execution completed through MicroPython raw REPL.",
        }
    )
    return result


def execute_code(
    port: str,
    code: str,
    *,
    baudrate: int = 115200,
    timeout_ms: int = 3000,
) -> dict[str, Any]:
    serial_mod = get_serial_module()
    if serial_mod is None:
        return {
            "ok": False,
            "error_kind": "pyserial_missing",
            "message": "pyserial is not installed.",
            "stdout": "",
            "stderr": "",
        }

    if not code.strip():
        return {
            "ok": False,
            "error_kind": "empty_code",
            "message": "No code was provided.",
            "stdout": "",
            "stderr": "",
        }

    timeout_s = max(timeout_ms, 100) / 1000
    ser: Any | None = None
    result: dict[str, Any] | None = None
    pre_action_output = ""
    repl_entry_output = ""
    stdout = ""
    stderr = ""
    execution_payload = b""
    entered_raw_repl = False
    execution_acknowledged = False
    stdout_eot_observed = False
    stderr_eot_observed = False
    raw_repl_prompt_observed = False
    execution_completed = False
    open_attempted = False
    control_lines_preconfigured = False
    physical_reset_excluded = True
    failure_stage: str | None = None
    operation_stage = "serial_open"
    lifecycle_cleanup_errors: list[str] = []
    post_operation_cleanup_errors: list[str] = []
    exit_error: Exception | None = None
    exit_failure_stage: str | None = None
    raw_repl_exit_sent = False
    raw_repl_exit_write_count = 0
    raw_repl_exit_confirmed = False

    try:
        open_attempted = True
        ser = open_serial_with_inactive_control_lines(
            serial_mod,
            port,
            baudrate=baudrate,
            timeout=0.1,
            write_timeout=1.0,
        )
        control_lines_preconfigured = True
        physical_reset_excluded = False
        operation_stage = "pre_action_read"
        pre_action_output = _read_until(ser, (), 0.1).decode(
            "utf-8",
            errors="replace",
        )

        operation_stage = "interrupt_write"
        _write_exact(ser, b"\r\x03\x03")
        time.sleep(0.1)
        operation_stage = "entry_prefix_read"
        entry_prefix = ser.read(4096)

        banner = b""
        for _attempt in range(3):
            operation_stage = "raw_repl_enter_write"
            _write_exact(ser, b"\x01")
            operation_stage = "raw_repl_enter_read"
            banner += _read_until(ser, (RAW_REPL_PROMPT,), timeout_s)
            entered_raw_repl = banner.endswith(RAW_REPL_PROMPT)
            if entered_raw_repl:
                break
            time.sleep(0.1)
        repl_entry_output = (entry_prefix + banner).decode(
            "utf-8",
            errors="replace",
        )
        if not entered_raw_repl:
            stderr = banner.decode("utf-8", errors="replace")
            result = {
                "ok": False,
                "error_kind": "raw_repl_enter_failed",
                "message": "Timed out waiting for raw REPL prompt.",
                "stdout": stdout,
                "stderr": stderr,
            }
        else:
            operation_stage = "code_write"
            _write_exact(ser, code.encode("utf-8") + b"\x04")
            operation_stage = "execution_read"
            execution_payload = _read_execution_frame(ser, timeout_s)
            result = _parse_execution_payload(execution_payload)
            stdout = str(result["stdout"])
            stderr = str(result["stderr"])
            execution_acknowledged = bool(
                result["execution_acknowledged"]
            )
            stdout_eot_observed = bool(result["stdout_eot_observed"])
            stderr_eot_observed = bool(result["stderr_eot_observed"])
            raw_repl_prompt_observed = bool(
                result["raw_repl_prompt_observed"]
            )
            execution_completed = bool(result["execution_completed"])
    except Exception as exc:
        lifecycle = serial_lifecycle_details(exc)
        if lifecycle is not None:
            open_attempted = bool(lifecycle["open_attempted"])
            control_lines_preconfigured = lifecycle["stage"] in {
                "open",
                "post_open_control_lines",
            }
            physical_reset_excluded = not open_attempted
            failure_stage = str(lifecycle["stage"])
            lifecycle_cleanup_errors.extend(lifecycle["cleanup_errors"])
        else:
            physical_reset_excluded = not open_attempted
            failure_stage = operation_stage
        result = {
            "ok": False,
            "error_kind": "raw_repl_io_error",
            "message": f"{type(exc).__name__}: {exc}",
            "stdout": stdout,
            "stderr": stderr,
        }
    finally:
        if ser is not None:
            if entered_raw_repl:
                try:
                    written = ser.write(b"\x02")
                    if written != 1:
                        exit_failure_stage = "raw_repl_exit_write"
                        raise OSError(
                            "Serial short write: expected 1 byte, "
                            f"wrote {written}."
                        )
                    raw_repl_exit_sent = True
                    raw_repl_exit_write_count = 1
                except Exception as exc:
                    exit_error = exc
                    if exit_failure_stage is None:
                        exit_failure_stage = "raw_repl_exit"
            post_operation_cleanup_errors.extend(
                deactivate_and_close_serial(ser)
            )

    if result is None:
        result = {
            "ok": False,
            "error_kind": "raw_repl_io_error",
            "message": "Raw REPL execution ended without a result.",
            "stdout": stdout,
            "stderr": stderr,
        }
        failure_stage = "result"

    cleanup_errors = lifecycle_cleanup_errors + post_operation_cleanup_errors
    result["pre_action_output"] = pre_action_output
    result["repl_entry_output"] = repl_entry_output
    result["raw_repl_entered"] = entered_raw_repl
    result["execution_acknowledged"] = execution_acknowledged
    result["stdout_eot_observed"] = stdout_eot_observed
    result["stderr_eot_observed"] = stderr_eot_observed
    result["raw_repl_prompt_observed"] = raw_repl_prompt_observed
    result["execution_completed"] = execution_completed
    result["execution_response_bytes"] = len(execution_payload)
    result["control_lines_preconfigured"] = control_lines_preconfigured
    result["physical_reset_excluded"] = physical_reset_excluded
    result["raw_repl_exit_sent"] = raw_repl_exit_sent
    result["raw_repl_exit_write_count"] = raw_repl_exit_write_count
    result["raw_repl_exit_confirmed"] = raw_repl_exit_confirmed
    result["raw_repl_exit_completed"] = raw_repl_exit_confirmed
    result["cleanup_completed"] = not cleanup_errors
    result["serial_cleanup_completed"] = not cleanup_errors
    result["cleanup_errors"] = cleanup_errors

    if exit_error is not None or post_operation_cleanup_errors:
        previous_error_kind = result.get("error_kind")
        result["operation_error_kind"] = previous_error_kind
        if previous_error_kind in {
            "raw_repl_enter_failed",
            "raw_repl_execute_failed",
            "raw_repl_completion_unconfirmed",
        }:
            result["protocol_error_kind"] = previous_error_kind
        elif "protocol_error_kind" in result:
            del result["protocol_error_kind"]
        if previous_error_kind and previous_error_kind != "raw_repl_io_error":
            result["operation_error_kind"] = previous_error_kind
        details: list[str] = []
        if exit_error is not None:
            details.append(
                f"raw_repl_exit: {type(exit_error).__name__}: {exit_error}"
            )
        details.extend(post_operation_cleanup_errors)
        result["ok"] = False
        result["error_kind"] = "raw_repl_io_error"
        result["failure_stage"] = (
            exit_failure_stage if exit_error is not None else "cleanup"
        )
        result["message"] = (
            f"{result.get('message', 'Raw REPL execution failed.')} "
            f"Serial finalization failed: {'; '.join(details)}"
        )
    elif failure_stage is not None:
        result["failure_stage"] = failure_stage

    return result
