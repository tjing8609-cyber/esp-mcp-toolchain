from __future__ import annotations

import time
from typing import Literal

from ..backends.pyserial_backend import (
    configure_serial_with_inactive_control_lines,
    deactivate_and_close_serial,
    get_serial_module,
    reassert_inactive_control_lines,
)
from ..config import get_selected_port
from ..errors import execution_error
from .log_tools import logged_task


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _monotonic() -> float:
    return time.monotonic()


def _write_byte(ser: object, value: bytes) -> None:
    written = ser.write(value)  # type: ignore[attr-defined]
    if written is not None and written != len(value):
        raise OSError(f"Serial write was incomplete: expected {len(value)} byte, wrote {written}.")


def _read_for(ser: object, duration_s: float, *, max_bytes: int) -> tuple[bytes, bool]:
    end_at = _monotonic() + max(duration_s, 0.0)
    buffer = bytearray()
    while len(buffer) < max_bytes and _monotonic() < end_at:
        data = ser.read(min(4096, max_bytes - len(buffer)))  # type: ignore[attr-defined]
        if data:
            buffer.extend(data)
    return bytes(buffer), len(buffer) >= max_bytes


@logged_task(task_type="reset", selected_port_arg="port", payload_args=("mode",))
def esp_reset(port: str | None = None, mode: Literal["soft", "hard"] = "soft") -> dict:
    state = {
        "serial_opened": False,
        "control_lines_preconfigured": False,
        "reset_command_sent": False,
        "hard_reset_pulse_started": False,
        "hard_reset_pulse_completed": False,
        "hard_reset_line_released": False,
        "reset_confirmed": False,
        "physical_reset_excluded": True,
        "pre_action_window_ms": 250,
        "pre_action_bytes_read": 0,
        "pre_action_output_observed": False,
        "pre_action_capture_limit_reached": False,
        "output_capture_limit_reached": False,
        "output_causality_confirmed": False,
        "cleanup_required": False,
        "cleanup_attempted": False,
        "cleanup_completed": True,
        "cleanup_errors": [],
        "failure_stage": None,
    }
    if mode not in {"soft", "hard"}:
        return execution_error(
            "unsupported_reset_mode",
            f"Unsupported reset mode: {mode}",
            tool="esp_reset",
            implemented=True,
            tool_name="esp_reset",
            tools名称="esp_reset",
            tools鍚嶇О="esp_reset",
            mode=mode,
            data=dict(state),
            suggested_next_actions=["Use mode=soft for MicroPython", "Use mode=hard to restart the running firmware"],
            **state,
        )

    serial_mod = get_serial_module()
    if serial_mod is None:
        return execution_error(
            "pyserial_missing",
            "pyserial is not installed.",
            tool="esp_reset",
            implemented=True,
            tool_name="esp_reset",
            tools名称="esp_reset",
            tools鍚嶇О="esp_reset",
            port=port,
            mode=mode,
            data=dict(state),
            suggested_next_actions=["Install requirements.txt", "Run python -m pip install pyserial"],
            **state,
        )

    selected_port = port or get_selected_port()
    if not selected_port:
        return execution_error(
            "serial_port_not_selected",
            "No serial port was provided or selected.",
            tool="esp_reset",
            implemented=True,
            tool_name="esp_reset",
            tools名称="esp_reset",
            tools鍚嶇О="esp_reset",
            port=None,
            mode=mode,
            data=dict(state),
            suggested_next_actions=["Run esp_port_list", "Run esp_port_select with the confirmed board port"],
            **state,
        )

    chunks: list[str] = []
    pre_action_text = ""
    ser = None
    operation_error: Exception | None = None
    cleanup_errors: list[str] = []
    failure_stage = "setup"
    try:
        ser = serial_mod.Serial()
        state["cleanup_required"] = True
        configure_serial_with_inactive_control_lines(
            ser,
            port=selected_port,
            baudrate=115200,
            timeout=0.1,
            write_timeout=1.0,
        )
        state["control_lines_preconfigured"] = True
        failure_stage = "open"
        state["physical_reset_excluded"] = False
        ser.open()
        state["serial_opened"] = True

        # Re-apply the inactive state after open in case the driver changed it.
        failure_stage = "post_open_control_lines"
        reassert_inactive_control_lines(ser)

        # Preserve a short pre-action window instead of clearing the input
        # buffer. Output here can reveal a driver/control-line side effect
        # caused by opening the port, and must not be mixed with reset output.
        failure_stage = "pre_action_capture"
        pre_action_raw, pre_action_limit_reached = _read_for(ser, 0.25, max_bytes=4096)
        pre_action_text = pre_action_raw.decode("utf-8", errors="replace")
        state["pre_action_bytes_read"] = len(pre_action_raw)
        state["pre_action_output_observed"] = bool(pre_action_raw)
        state["pre_action_capture_limit_reached"] = pre_action_limit_reached

        if mode == "soft":
            failure_stage = "soft_interrupt"
            _write_byte(ser, b"\x03")
            _sleep(0.1)
            failure_stage = "soft_reset_command"
            _write_byte(ser, b"\x04")
            state["reset_command_sent"] = True
        else:
            # Match esptool's control-line semantics while keeping IO0 high.
            failure_stage = "hard_prepare"
            ser.dtr = False
            failure_stage = "hard_assert"
            ser.rts = True
            state["hard_reset_pulse_started"] = True
            delay_error: Exception | None = None
            try:
                failure_stage = "hard_delay"
                _sleep(0.1)
            except Exception as exc:
                delay_error = exc
            release_error: Exception | None = None
            try:
                failure_stage = "hard_release"
                ser.rts = False
                state["hard_reset_line_released"] = True
                state["hard_reset_pulse_completed"] = delay_error is None
            except Exception as exc:
                release_error = exc
            if delay_error is not None:
                failure_stage = "hard_delay"
                if release_error is not None:
                    raise RuntimeError(
                        f"{delay_error}; additionally failed to release RTS: {release_error}"
                    ) from delay_error
                raise delay_error
            if release_error is not None:
                failure_stage = "hard_release"
                raise release_error

        failure_stage = "capture"
        output_raw, output_limit_reached = _read_for(ser, 2.0, max_bytes=65536)
        state["output_capture_limit_reached"] = output_limit_reached
        if output_raw:
            chunks.append(output_raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        operation_error = exc
    finally:
        if ser is not None:
            state["cleanup_attempted"] = True
            cleanup_errors.extend(deactivate_and_close_serial(ser))
            if (
                state["hard_reset_pulse_started"]
                and not any(item.startswith("rts_cleanup:") for item in cleanup_errors)
            ):
                state["hard_reset_line_released"] = True

    text = "".join(chunks)
    state["cleanup_completed"] = not cleanup_errors
    state["cleanup_errors"] = cleanup_errors
    if operation_error is not None or cleanup_errors:
        state["failure_stage"] = failure_stage if operation_error is not None else "cleanup"
        if operation_error is not None:
            message = f"{type(operation_error).__name__}: {operation_error}"
            error_kind = "reset_failed"
        else:
            message = "Reset action was sent, but serial cleanup did not complete safely."
            error_kind = "reset_cleanup_failed"
        if cleanup_errors:
            message = f"{message} Cleanup: {'; '.join(cleanup_errors)}"
        return execution_error(
            error_kind,
            message,
            tool="esp_reset",
            port=selected_port,
            mode=mode,
            implemented=True,
            tool_name="esp_reset",
            tools名称="esp_reset",
            tools鍚嶇О="esp_reset",
            text=text,
            pre_action_text=pre_action_text,
            data={"text": text, "pre_action_text": pre_action_text, **state},
            suggested_next_actions=[
                "Check the port name against esp_port_list",
                "Close other serial monitors",
                "Inspect failure_stage and cleanup_errors",
            ],
            **state,
        )

    message = (
        "MicroPython soft-reset command was sent; this call did not independently confirm the restart."
        if mode == "soft"
        else "Hardware-reset pulse was sent; this call did not independently confirm the restart."
    )
    if state["pre_action_output_observed"]:
        message += " Pre-action serial output was observed; inspect pre_action_text for an open-time side effect."
    return {
        "ok": True,
        "tool": "esp_reset",
        "tool_name": "esp_reset",
        "tools名称": "esp_reset",
        "tools鍚嶇О": "esp_reset",
        "implemented": True,
        "port": selected_port,
        "mode": mode,
        "text": text,
        "pre_action_text": pre_action_text,
        "message": message,
        "data": {"text": text, "pre_action_text": pre_action_text, **state},
        **state,
    }
